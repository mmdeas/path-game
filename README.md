path-game
=========

A server and client for a networked game where the aim is as-yet loosely 
defined but involves creating a path from a start to an end node better than 
the other players.

This path is created across an image which can be used to define the cost of 
the path (cost of traversing a pixel relative to colour or change in colour 
between adjacent pixels, for example) and which the players modify as they 
create their paths.

Being the first thing I've created with Twisted, the project lacks tests, is
undoubtedly buggy and does not lack in areas for improvement. GitHub issues, 
pull requests and critique welcome.

Future
------

* Allow player to specify parent.
* Client-side support for the chat feature.
* Automation for clients - pick/write an algorithm rather than manually expanding.
* Many things to make it nicer to play.

License
-------

Available under MIT license - see LICENSE for details.
