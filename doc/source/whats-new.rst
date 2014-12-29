==========================
What's new in FLAC Manager
==========================

Current release: |release|
==========================
* fixed a priority queueing bug where relatively small encoded files would
  misreport their actual statuses
* the wait time between all :meth:`queue.Queue.get_nowait` calls is now
  configurable via :data:`flacmanager.QUEUE_GET_NOWAIT_AFTER`
* tested on Mac OS X 10.10.1

Previous releases
=================

Release 0.6.0
-------------
* updated to use Mac OS X `diskutil
  <https://developer.apple.com/library/mac/documentation/Darwin/Reference/Manpages/man8/diskutil.8.html>`_
  instead of ``disktool``
* updated documentation to reflect necessary changes caused by Mac OS X 10.9.2
  upgrade
* tested on Mac OS X 10.9.2

Release 0.5.0
-------------
* added offline metadata editing support

Release 0.4.2
-------------
* added a note in :doc:`usage` regarding metadata persistence
* fixed a bug in FLAC encoding status reporting where the status updates would
  accumulate, incrementally growing the status line in length rather than
  "refreshing" the percent complete and ratio display (caused by recent updates
  to python3.3 and/or flac ports)

Release 0.4.1
-------------
* updated documentation to reflect correct version

Release 0.4.0
-------------
* added ability persist and restore user-specified metadata per disc (easier
  recovery from ripping errors - no need to re-enter information)
* fixed a bug in configuration editor where the MP3 ``library_root`` setting
  was not being updated

Release 0.3.0
-------------
* support for encoding MP3s in a location different than for FLACs (easier
  uploading via Google Play Music Manager)

Release 0.2.0
-------------
* fixed missing blockquote of configuration sample on the :doc:`usage` page
* fixed a bug in :func:`flacmanager.get_disc_info` (faulty regex) where an
  inserted disc with an apostrophe in the disc title would cause the mountpoint
  to be misreported
* new default naming for album folder and file names based on disc number,
  track number, compilation
* fixed :class:`flacmanager.AboutDialog` to read license from *LICENSE.txt*
  instead of ``flacmanager.__doc__``

