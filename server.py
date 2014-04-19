#! /usr/bin/env python

# Copyright 2014 Miguel Martinez de Aguirre
# See LICENSE for details.

from itertools import product
from Queue import Queue, Empty
from random import randint
from StringIO import StringIO

from PIL import Image
import Tkinter

from twisted.cred import checkers, portal, credentials
from twisted.python import log
from twisted.internet import defer
from twisted.spread import pb

from zope.interface import implements

import error
import util

VERSION = 2
CLIENT_VERSIONS = (2,)


class Server(object):
    clients = {}
    started = False
    finished = False
    moves = Queue()

    def __init__(self, image, maxPlayers=float('inf'), **kwargs):
        """
        Game clients will be capped at maxPlayers, if provided.
        **kwargs are passed to a new GameType.
        See GameType documentation for more details.
        """
        log.msg(["Server.__init__", self, image, maxPlayers, kwargs])
        self.gameType = GameType(image, **kwargs)
        self.maxPlayers = maxPlayers
        im = Image.open(StringIO(self.gameType.image)).convert("RGB")
        self.costs = im.load()
        self.imageSize = im.size
        self.colours = self._generateColours()

    def start(self):
        """Make new connections chat-only and initialise players."""
        log.msg(["start", self])
        if self.started:
            return
        self.started = True
        self.turns = 0
        self.players = self.clients.values()
        players = [(c, self.clients[c].colour) for c in self.clients]
        size = self.imageSize
        if len(players) <= 4:
            # If possible, use corners
            startPoints = [(0, size[1]-1), (size[0]-1, 0),
                           (size[0]-1, size[1]-1), (0, 0)]
            endPoints = [startPoints[1], startPoints[0],
                         startPoints[3], startPoints[2]]
        else:
            # Otherwise have random start points and
            # finish in the opposite quarter
            startPoints = []
            endPoints = []
            for p in players:
                startPoints.append((randint(0, size[0]-1),
                                    randint(0, size[1]-1)))
                endPoints.append(((startPoints[-1][0] + size[0]/2) % size[0],
                                  (startPoints[-1][1] + size[1]/2) % size[1]))
        gameStartInfo = []
        costdelta = []
        for p in self.players:
            gameStartInfo.append((p, startPoints.pop(), endPoints.pop()))
            costdelta.append((gameStartInfo[-1][1], p.colour))
        for info in gameStartInfo:
            info[0].gameStarted(info[1], info[2], players, costdelta)
        reactor.callInThread(self.doMoves)

    def doMoves(self):
        log.msg(["doMoves", self])
        if not len(self.players):
            log.msg("No players. Immediate finish.")
            self.finished = True
        while not self.finished:
            self.turns += 1
            log.msg("Starting next round.")
            turn = []
            for i in xrange(len([p for p in self.players if not p.finished])):
                try:
                    t = self.gameType.timeout / 1000.0 * 1.5
                    turn.append(self.moves.get(True, t))  # (client, node)
                except Empty, e:
                    log.msg("Client timed out.")
                    log.err(e, "{0}/{1} clients did not send a move. Clients who did: \n\t{2}"
                            .format(
                                len(self.players) - i,
                                len(self.players),
                                turn)
                            )
            assert self.moves.empty()
            costdelta = []
            for t in turn:
                if t == ():  # no-op
                    continue
                if t[1] == t[0].end:
                    t[0].calculateScore(self.turns)
                    t[0].finished = True
                cost = self.costs[t[1]]
                colour = t[0].colour
                newcost = map(lambda a, b: (a+b)/2, cost, colour)
                costdelta.append((t[1], newcost))
                self.costs[t[1][0], t[1][1]] = tuple(newcost)
            for p in self.players:
                p.startNextTurn(costdelta)
            # finish when all players are done
            if all([p.finished for p in self.players]):
                self.finished = True
        log.msg("Game finished.")
        scores = [(p.score, p) for p in self.players]
        scores.sort()
        log.msg(["Scores:", scores])
        scores[0][1].win(scores)
        for s, p in scores[1:]:
            p.gameOver(scores)

    def sendMessage(self, client, message):
        log.msg(["sendMessage", self, client, message])
        message = '<{0}> {1}'.format(client.name, message)
        for c in self.clients.values():
            c.sendChat(message, client.colour)

    def addClient(self, client):
        log.msg(["addClient", self, client, len(self.clients), self.maxPlayers])
        self.clients[client.name] = client
        if len(self.clients) >= self.maxPlayers:
            self.start()

    def removeClient(self, client):
        log.msg(["removeClient", self, client])
        del self.clients[client.name]

    def isNameAvailable(self, name):
        log.msg(["isNameAvailable", self, name])
        return name not in self.clients

    def _generateColours(self):
        for c in product((0, 255), (0, 255), (0, 255)):
            if c[0] == c[1] == c[2]:
                continue
            log.msg(["colours.next", c])
            yield c
        while 1:
            c = (randint(0, 255), randint(0, 255), randint(0, 255))
            log.msg(["colours.next", c])
            yield c


class ChatClient(pb.Avatar):
    """Avatar for client with the ability to get a name and chat."""
    colour = (0, 0, 0)  # default colour for chat-only clients
    remote = None

    def __init__(self, server, name):
        log.msg(["ChatClient.__init__", self, server, name])
        self.server = server
        self.name = name

    def __repr__(self):
        try:
            return "<{0}.{1} instance '{2}'>".format(self.__module__,
                                                     self.__class__.__name__,
                                                     self.name)
        except AttributeError:
            return "<{0}.{1} instance>".format(self.__module__,
                                               self.__class__.__name__)

    def perspective_message(self, message):
        self.server.sendMessage(self, message)

    def perspective_canPlay(self):
        log.msg(["canPlay returning False", self])
        return False

    def sendChat(self, message, colour):
        log.msg(["sendChat", self, message, colour])
        d = self.remote.callRemote("print", message, colour)
        d.addErrback(self._errored)

    def attached(self, mind):
        log.msg(["attached", self, mind])
        self.remote = mind
        self.server.addClient(self)

    def detached(self, mind):
        log.msg(["detached", self, mind])
        self.remote = None
        self.server.removeClient(self)

    def _errored(self, reason):
        log.err(reason, self)


class GameClient(ChatClient):
    """Client expected to play the game as well as being able to chat."""
    ready = False
    finished = False

    def __init__(self, server, name):
        ChatClient.__init__(self, server, name)
        log.msg(["GameClient.__init__", self, server, name])
        self.colour = self.server.colours.next()

    def perspective_getGameType(self):
        log.msg(["getGameType", self])
        return self.server.gameType.toDictionary()

    def perspective_getColour(self):
        log.msg(["getColour", self])
        return self.colour

    def perspective_canPlay(self):
        log.msg(["canPlay returning True", self])
        return True

    def gameStarted(self, start, end, players, costdelta):
        log.msg(["gameStarted", start, end, players, costdelta])
        self.visited = {start: ()}
        self.childMaker = util.ChildMaker(self.server.imageSize,
                                          self.server.gameType.diagonals)
        self.start = start
        self.end = end
        d = self.remote.callRemote("startGame", start, end, players, costdelta)
        d.addErrback(self._errored)

    def startNextTurn(self, costdelta):
        log.msg(["startNextTurn", costdelta])
        if self.finished:
            d = self.remote.callRemote("updateCosts", costdelta)
        else:
            d = self.remote.callRemote("startNextTurn", costdelta)
        d.addErrback(self._errored)

    def perspective_expandNode(self, node, parent):
        log.msg(["expandNode", self, node, parent])
        if node == ():  # no-op
            self.server.moves.put(())
            return
        log.msg({"parent": parent, "visited": self.visited, "node": node,
                 "children": self.childMaker.getChildren(parent)})
        if parent not in self.visited \
                or node not in self.childMaker.getChildren(parent):
            raise error.IllegalNodeExpansion()
        self.visited[node] = parent
        self.server.moves.put((self, node))

    def calculateScore(self, turns):
        score = 0
        node = self.end
        parent = self.visited[node]
        while parent != ():
            score += util.cost(node, parent, self.server.costs)
            node, parent = parent, self.visited[parent]
        self.score = score + turns

    def win(self, scores):
        scores = [(s, p.name) for s, p in scores]
        self.remote.callRemote("win", scores)

    def gameOver(self, scores):
        scores = [(s, p.name) for s, p in scores]
        self.remote.callRemote("gameOver", scores)


class GameType(object):
    """
    Defines the type of game being hosted.
    A race has each player facing the same challenge independently.
    A battle places multiple players on the same image with different
    goals for each player and interactions between them possible.
    """
    def __init__(self, image, game='race', timeout=1000, automated=False,
                 diagonals=True):
        """
        image: filename of image to use for the game
        game: one of 'race' or 'battle'
        timeout: the time in ms a client waits for user input before sending a
                 no-op.
        automated: boolean giving whether scripting is allowed in the client.
        """
        log.msg(["GameType.__init__", self, image, game,
                timeout, automated, diagonals])
        self.game = game
        self.timeout = int(timeout)
        self.automated = bool(int(automated))
        if image is not None:
            self.image = file(image, 'rb').read()
        self.diagonals = diagonals

    def toDictionary(self):
        """Save game type information to a dictionary."""
        log.msg(["toDictionary", self])
        return {
            'game': self.game,
            'timeout': self.timeout,
            'automated': self.automated,
            'image': self.image,
            'diagonals': self.diagonals,
            }

    def fromDictionary(self, d):
        """Restore game type information from a dictionary."""
        log.msg(["fromDictionary", self])
        self.game = d['game']
        self.timeout = int(d['timeout'])
        self.automated = bool(d['automated'])
        self.image = d['image']
        self.diagonals = d['diagonals']


class Realm(object):
    implements(portal.IRealm)

    def requestAvatar(self, avatarID, mind, *interfaces):
        assert pb.IPerspective in interfaces
        if self.server.started:
            avatar = ChatClient(self.server, avatarID)
        else:
            avatar = GameClient(self.server, avatarID)
        avatar.attached(mind)
        return pb.IPerspective, avatar, lambda: avatar.detached(mind)


class UsernameOnlyChecker(object):
    implements(checkers.ICredentialsChecker)
    credentialInterfaces = credentials.IUsernamePassword, \
        credentials.IUsernameHashedPassword

    def __init__(self, server):
        self.server = server

    def requestAvatarId(self, credentials):
        log.msg(["requestAvatarId", credentials])
        if not self.server.isNameAvailable(credentials.username):
            log.msg("Request failed. Name taken.")
            return defer.fail(error.NameTaken())
        else:
            log.msg("Request succeeded.")
            return defer.succeed(credentials.username)


if __name__ == '__main__':
    from sys import argv, stdout
    from twisted.internet import reactor, tksupport
    log.startLogging(stdout, setStdout=False)
    if len(argv) == 1 or argv[1] == "--help":
        print "usage: {0} image [maxplayers [game=race|battle] [timeout=ms] [automated=0|1]]".format(argv[0])
        exit(1)
    elif len(argv) == 2:
        server = Server(argv[1])
    elif len(argv) == 3:
        server = Server(argv[1], argv[2])
    else:
        server = Server(argv[1], argv[2],
                        **dict([tuple(a.split("=")) for a in argv[3:]]))
    log.msg("Starting server with protocol version", VERSION)
    log.msg("Accepted client versions are", CLIENT_VERSIONS)
    realm = Realm()
    realm.server = server
    checker = UsernameOnlyChecker(realm.server)
    p = portal.Portal(realm, [checker])
    reactor.listenTCP(8181, pb.PBServerFactory(p))

    def startServer():
        b.config(state=Tkinter.DISABLED, text="Started")
        server.start()

    def stopServer():
        server.finished = True
        reactor.stop()

    root = Tkinter.Tk()
    root.protocol("WM_DELETE_WINDOW", stopServer)
    b = Tkinter.Button(root, text=" Start ", command=startServer)
    b.pack()
    tksupport.install(root)

    reactor.run()