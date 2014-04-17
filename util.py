# Copyright 2014 Miguel Martinez de Aguirre
# See LICENSE for more details.

from itertools import product

from twisted.python import log


class ChildMaker(object):
    def __init__(self, size, diagonals):
        """
        size: (width, height) of the image
        diagonals: boolean indicating whether diagonals are allowed
        """
        self.size = size
        self.diagonals = diagonals

    def getChildren(self, node):
        """
        node: (x, y) coordinates of parent for children
        returns: set of viable children
        """
        log.msg(["getChildren", self, node])
        children = set()
        if not self.diagonals:
            vectors = [(-1, 0), (1, 0), (0, 1), (0, -1)]
        else:
            vectors = [x for x in product((-1, 0, 1), (-1, 0, 1)) if x != (0, 0)]
        for v in vectors:
            child = (node[0] + v[0], node[1] + v[1])
            if (0 <= child[0] < self.size[0]) and (0 <= child[1] < self.size[1]):
                children.add(child)
        return children
