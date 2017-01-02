=========================
What's new in FLACManager
=========================

Current release: |release|
==========================
* standardized the application menubar
  (now has conventional File, Edit and Help menus)
* directory and file naming for music library can now be configured via
  the Edit menu (rather than needing to edit the source code)
* comprehensive tag management: you can now configure\ :sup:`†` (via the
  Edit menu) how metadata fields in the UI map to Vorbis comments for
  FLAC files and ID3v2 tags for MP3 files
* custom Vorbis comments and ID3v2 tags can now be added on a per-album
  and per-track basis
* clipping is now detected automatically when encoding MP3s, and
  FLACManager will re-encode with scaled PCM data until no clipping
  occurs
* the selected/entered metadata can now be persisted at any time using
  the File | Save metadata menu command (metadata is **always**
  persisted automatically when the [Rip and Tag] button is clicked)
* the UI now formally includes an input field for an album's record
  label\ :sup:`††`

:sup:`†` defaults have been tuned to produce sane results for Apple
iTunes, Google Play Music, and any FLAC player that respects
`Ogg Vorbis I format specification: comment field and header
specification <https://xiph.org/vorbis/doc/v-comment.html>`_

:sup:`††` be aware that neither Gracenote nor MusicBrainz currently
include an album's record label in their respective metadata; a value
must be provided by hand

Previous releases
=================

Release 0.7.2
-------------
* now persists metadata *before* preparing to encode the CD-DA data
  (see https://github.com/mzipay/FLACManager/issues/1)
* tested on Mac OS X 10.11.5

Release 0.7.1
-------------
* fixed a bug in the disc check loop caused by a change to ``diskutil`` output
  in El Capitan
* relaxed the SSL certificate verification when connecting to an HTTPS source
  for metadata or cover images
* tested on Mac OS X 10.11.1 (El Capitan)

Release 0.7.0
-------------
* fixed a priority queueing bug where relatively small encoded files would
  misreport their actual statuses
* the wait time between all :meth:`queue.Queue.get_nowait` calls is now
  configurable via :data:`flacmanager.QUEUE_GET_NOWAIT_AFTER`
* tested on Mac OS X 10.10.1

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

