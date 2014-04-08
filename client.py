#! /usr/bin/env python

# Copyright 2014 Miguel Martinez de Aguirre
# See LICENSE for details

from StringIO import StringIO
import sys

from PIL import Image, ImageTk
import Tkinter

from twisted.cred import credentials
from twisted.internet import reactor, tksupport
from twisted.protocols import basic
from twisted.python import log
from twisted.spread import pb

import error
from server import GameType
import util


class GameClient(pb.Referenceable):
	visited = set()
	def __init__(self, address="localhost", port=8181):
		log.msg(["GameClient.__init__", self, address, port])
		self.factory = pb.PBClientFactory()
		reactor.connectTCP(address, port, self.factory)

	def remote_print(self, message, colour):
		log.msg(["print", self, message, colour])
		chatui.printMessage(message, colour)

	def remote_win(self):
		print "Congrats!"
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
		self.later = reactor.callLater(self.gameType.timeout/1000., self._finishTurn)
		self.gameui.applyCostDelta(costdelta)
		self.gameui.setActive(True)

	def _finishTurn(self):
		log.msg(["_finishTurn", self])
		self.gameui.setActive(False)
		chosen = self.gameui.chosen
		log.msg({'chosen': chosen, 'visited': self.visited, 'parents': self.childMaker.getChildren(chosen) if chosen != () else None})
		if chosen == ():
			d = self.perspective.callRemote("expandNode", (), ())
		else:
			for parent in self.childMaker.getChildren(chosen):
				if parent in self.visited:
					self.visited.add(chosen)
					d = self.perspective.callRemote("expandNode", chosen, parent)
					break
			else:
				d = self.perspective.callRemote("expandNode", (), ())
		d.addErrback(self._errored)

	def finishTurnEarly(self):
		log.msg(["finishTurnEarly", self])
		self.later.cancel()
		self._finishTurn()

	def connect(self, name):
		log.msg(["connect", self, name])
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
			self.gameui = GameUI(self)
			d = self.perspective.callRemote("getGameType")
			d.addCallback(self._setGameType)
			d.addErrback(self._errored)
		else:
			log.msg("Acting as chat-only client.")
		# Have chat functionality for both
		self.chatui = ChatUI(self)

	def _setGameType(self, gameType):
		log.msg(["_setGameType", self, `gameType`[:100]])
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
		self.connect(self.name+"_")

	def _errored(self, reason):
		log.msg("Logging error:")
		log.err(reason)

	def shutdown(self):
		log.msg("Stopping reactor from " + `self`)
		reactor.stop()


class GameUI(object):
	active = False
	edited = []
	def __init__(self, conduit):
		"""conduit: a GameClient with a connection to a server."""
		super(GameUI, self).__init__()
		log.msg(["GameUI.__init__", self, conduit])
		self.conduit = conduit
		self.root = Tkinter.Tk()
		tksupport.install(self.root)
		self.root.protocol("WM_DELETE_WINDOW", self.conduit.shutdown)
		self.root.title("Most exciting game you've ever played.")
		self.canvas = Tkinter.Canvas(self.root, offset="10,10");
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
		self.edited.append((x,y, self.pa[x, y]))
		for xx in (x, x+self.factor-1):
			for yy in (y, y+self.factor-1):
				self.pa[xx, yy] = self.colour
		self._updateImage()
		self.conduit.finishTurnEarly()

	def setImage(self, image):
		"""image: a PIL.Image"""
		log.msg(["setImage", self, image])
		size = image.size
		self.image = self._resize(image.convert("RGB"), 9)
		self.pa = self.image.load()
		self._updateImage()

	def _updateImage(self):
		log.msg(["_updateImage", self])
		imagetk = ImageTk.PhotoImage(self.image)
		self.canvas.itemconfig(self.item, image=imagetk)
		self.canvas.config(width=imagetk.width(), height=imagetk.height())
		# keep reference so that old imagetk isn't GCed until new one is in place
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


class ChatUI(basic.LineReceiver):
	from os import linesep as delimeter
	def __init__(self, conduit):
		log.msg(["ChatUI.__init__", self, conduit])
		self.conduit = conduit

	def printMessage(self, message, colour):
		log.msg(["printMessage", self, message, colour])
		print message

	def lineReceived(self, line):
		log.msg(["lineReceived", self, line])
		self.conduit.sendMessage(line.strip())
		

if __name__ == '__main__':
	if len(sys.argv) >= 2 and sys.argv[1] == "--help":
		print "usage: {0} [address [port]]".format(argv[0])
		exit(0)
	log.startLogging(sys.stdout, setStdout=False)
	# TODO: for now, assume everyone is called 'bob'
	GameClient(*sys.argv[1:]).connect("bob")