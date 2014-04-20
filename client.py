#! /usr/bin/env python

# Copyright 2014 Miguel Martinez de Aguirre
# See LICENSE for details

from StringIO import StringIO
import sys

from PIL import Image, ImageTk
import Tkinter
from tkSimpleDialog import Dialog

from twisted.cred import credentials
from twisted.internet import reactor, tksupport
from twisted.protocols import basic
from twisted.python import log
from twisted.spread import pb

import error
from server import GameType
import util

VERSION = 2


class GameClient(pb.Referenceable):
    visited = set()
    repl = {'xander': 'Xandy Pandy',
            'alargeasteroid': 'A Large Asteroid',
            'moon': 'MooN'}

    def __init__(self, address="localhost", port=8181):
        log.msg(["GameClient.__init__", self, address, port])
        self.factory = pb.PBClientFactory()
        self.root = Tkinter.Tk()
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        tksupport.install(self.root)
        reactor.connectTCP(address, port, self.factory)

    def sendMessage(self, message):
        d = self.perspective.callRemote("message", message)
        d.addErrback(self._errored)

    def remote_print(self, message, colour):
        log.msg(["print", self, message, colour])
        self.chatui.printMessage(message, colour)

    def remote_win(self, scores):
        print "Congrats!"
        print scores
        self.gameui.setActive(False)

    def remote_gameOver(self, scores):
        print "Better luck next time!"
        print scores
        self.gameui.setActive(False)

    def remote_startGame(self, start, end, players, costdelta):
        """
        start: (x, y) start coordinate
        end: (x, y) aim coordinate
        players: [(name, colour), ...]
        costdelta: [(coordinate, colour), ...]
        """
        log.msg(["startGame", start, end, players, costdelta])
        self.start = start
        self.end = end
        self.players = players
        self.visited.add(start)
        # TODO: notify user of start
        self.remote_startNextTurn(costdelta)

    def remote_startNextTurn(self, costdelta):
        log.msg(["startNextTurn", self, costdelta])
        t = self.gameType.timeout / 1000.0
        self.later = reactor.callLater(t, self._finishTurn)
        self.gameui.applyCostDelta(costdelta)
        self.gameui.setActive(True)

    def remote_updateCosts(self, costdelta):
        log.msg(["updateCosts", costdelta])
        self.gameui.applyCostDelta(costdelta)

    def _finishTurn(self):
        log.msg(["_finishTurn", self])
        self.gameui.setActive(False)
        chosen = self.gameui.chosen
        log.msg({'chosen': chosen, 'visited': self.visited,
                 'parents': self.childMaker.getChildren(chosen) if chosen != () else None})
        if chosen == ():
            d = self.perspective.callRemote("expandNode", (), ())
        else:
            for parent in self.childMaker.getChildren(chosen):
                if parent in self.visited:
                    self.visited.add(chosen)
                    d = self.perspective.callRemote("expandNode", chosen,
                                                    parent)
                    break
            else:
                d = self.perspective.callRemote("expandNode", (), ())
        d.addErrback(self._errored)

    def finishTurnEarly(self):
        log.msg(["finishTurnEarly", self])
        self.later.cancel()
        self._finishTurn()

    def connect(self, name=None):
        log.msg(["connect", self, name])
        while name is None:
            dialog = NameDialog(self.root)
            name = dialog.result
        if name.lower() in self.repl:
            name = self.repl[name.lower()]
        self.name = name
        d = self.factory.login(credentials.UsernamePassword(self.name, ''),
                               client=self)
        d.addCallback(self._connected)
        d.addErrback(self._nameTaken)
        d.addErrback(self._errored)
        reactor.run()

    def _connected(self, perspective):
        log.msg(["Connected with name: " + self.name, self, perspective])
        self.perspective = perspective
        d = perspective.callRemote("canPlay")
        d.addCallback(self._setCanPlay)
        d.addErrback(self._errored)

    def _setCanPlay(self, canPlay):
        log.msg(["setCanPlay", self, canPlay])
        self.canPlay = canPlay
        if canPlay:
            log.msg("Acting as game client.")
            self.gameui = GameUI(self, self.root)
            d = self.perspective.callRemote("getGameType")
            d.addCallback(self._setGameType)
            d.addErrback(self._errored)
        else:
            log.msg("Acting as chat-only client.")
        # Have chat functionality for both
        self.chatui = ChatUI(self, self.root)

    def _setGameType(self, gameType):
        log.msg(["_setGameType", self, repr(gameType)[:100]])
        self.gameType = GameType(None)
        self.gameType.fromDictionary(gameType)
        image = Image.open(StringIO(self.gameType.image))
        self.gameui.setImage(image)
        self.childMaker = util.ChildMaker(image.size, self.gameType.diagonals)
        d = self.perspective.callRemote("getColour")
        d.addCallback(self._setColour)
        d.addErrback(self._errored)

    def _setColour(self, colour):
        log.msg(["_setColour", self, colour])
        self.gameui.colour = colour
        # Do things.

    def _nameTaken(self, failure):
        failure.trap(error.NameTaken)
        log.msg("Name '{0}' already taken. Retrying with new name.".format(self.name))
        name = None
        while name is None:
            dialog = NameDialog(self.root, True)
            name = dialog.result
        self.connect(name)

    def _errored(self, reason):
        log.msg("Logging error:")
        log.err(reason)

    def shutdown(self):
        log.msg("Stopping reactor from " + repr(self))
        reactor.stop()


class GameUI(object):
    active = False
    edited = []

    def __init__(self, conduit, root):
        """conduit: a GameClient with a connection to a server."""
        super(GameUI, self).__init__()
        log.msg(["GameUI.__init__", self, conduit, root])
        self.conduit = conduit
        self.window = Tkinter.Toplevel(root)
        self.window.title("Most exciting game you've ever played.")
        self.canvas = Tkinter.Canvas(self.window, offset="10,10")
        self.item = self.canvas.create_image(0, 0, anchor=Tkinter.NW)
        self.canvas.pack()
        self.canvas.bind("<Button 1>", self._onClick)

    def _onClick(self, event):
        log.msg(["_onClick", self, event, self.active])
        if not self.active:
            return
        self._unedit()
        # floor x,y to multiples of self.factor
        x, y = map(lambda a, f=self.factor: a/f*f, (event.x, event.y))
        self.chosen = (x/self.factor, y/self.factor)
        self.edited.append((x, y, self.pa[x, y]))
        for xx in (x, x+self.factor-1):
            for yy in (y, y+self.factor-1):
                self.pa[xx, yy] = self.colour
        self._updateImage()
        self.conduit.finishTurnEarly()

    def setImage(self, image):
        """image: a PIL.Image"""
        log.msg(["setImage", self, image])
        self.image = self._resize(image.convert("RGB"), 9)
        self.pa = self.image.load()
        self._updateImage()

    def _updateImage(self):
        log.msg(["_updateImage", self])
        imagetk = ImageTk.PhotoImage(self.image)
        self.canvas.itemconfig(self.item, image=imagetk)
        self.canvas.config(width=imagetk.width(), height=imagetk.height())
        # keep reference so that old imagetk isn't
        # GCed until new one is in place
        self.imagetk = imagetk

    def _resize(self, image, factor):
        log.msg(["_resize", image, factor])
        self.factor = factor
        opa = image.load()
        size = (image.size[0]*factor, image.size[1]*factor)
        im = Image.new("RGB", size, None)
        npa = im.load()
        for x in xrange(size[0]):
            for y in xrange(size[1]):
                npa[x, y] = opa[x/factor, y/factor]
        return im

    def setActive(self, active):
        log.msg(["setActive", self, active])
        self.active = active
        if active:
            self._unedit()
            self.chosen = ()
        # TODO: visually show whether active or inactive

    def _unedit(self):
        log.msg(["_unedit", self, self.edited])
        for e in self.edited:
            for x in xrange(e[0], e[0]+self.factor):
                for y in xrange(e[1], e[1]+self.factor):
                    self.pa[x, y] = e[2]
        self.edited = []
        self._updateImage()

    def applyCostDelta(self, costdelta):
        log.msg(["applyCostDelta", self, costdelta])
        self._unedit()
        for pixel, colour in costdelta:
            for x in xrange(pixel[0]*self.factor, (pixel[0]+1)*self.factor):
                for y in xrange(pixel[1]*self.factor, (pixel[1]+1)*self.factor):
                    self.pa[x, y] = tuple(colour)
        self._updateImage()


class ChatUI(object):
    tags = {}

    def __init__(self, conduit, root):
        log.msg(["ChatUI.__init__", self, conduit])
        self.conduit = conduit
        self.window = root
        self.chatlog = Tkinter.Text(self.window, state='disabled', wrap='word',
                                    width=80, height=24)
        self.typebox = Tkinter.Entry(self.window, width=80)
        self.typebox.bind('<Return>', self.returnPressed)
        self.typebox.pack(fill='both', expand='yes')
        self.chatlog.pack(fill='both', expand='yes')

    def printMessage(self, message, colour):
        # TODO: use colour
        log.msg(["printMessage", self, message, colour])
        tag = self.getTag(colour)
        self.chatlog['state'] = 'normal'
        self.chatlog.insert('end', message, (tag, ))
        self.chatlog.insert('end', '\n')
        self.chatlog['state'] = 'disabled'
        print message

    def returnPressed(self, event):
        log.msg(["returnPressed", self, event])
        self.conduit.sendMessage(self.typebox.get())
        self.typebox.delete('0', 'end')

    def getTag(self, colour):
        try:
            return self.tags[colour]
        except KeyError:
            r, g, b = colour
            yiq = (r * 299 + g * 587 + b * 114) / 1000
            if yiq > 128:
                fg = 'black'
            else:
                fg = 'white'
            hexcolour = '#' + ''.join('%02x' % c for c in colour)
            name = ''.join(('tag', hexcolour))
            self.chatlog.tag_configure(name, background=hexcolour,
                                       foreground=fg)
            self.tags[colour] = name
            return name


class NameDialog(Dialog):
    """Requests name from user."""
    def __init__(self, master, wasTaken=False):
        self.wasTaken = wasTaken
        Dialog.__init__(self, master)

    def body(self, master):
        if self.wasTaken:
            text = "Username taken. Try again."
        else:
            text = "Enter username:"
        Tkinter.Label(master, text=text).pack()
        self.entry = Tkinter.Entry(master)
        self.entry.pack()
        return self.entry

    def apply(self):
        self.result = self.entry.get()


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == "--help":
        print "usage: {0} [address [port]]".format(sys.argv[0])
        exit(0)
    log.startLogging(sys.stdout, setStdout=False)
    GameClient(*sys.argv[1:]).connect()
