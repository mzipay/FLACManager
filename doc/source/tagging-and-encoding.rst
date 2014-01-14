==============================
Audio tagging and encoding API
==============================

The ``flacmanager.FLAC_*_TEMPLATE`` string formats are used to create the
directory and file names for *.flac* and *.mp3* files. These string formats
take their substitution values from a dictionary of metadata items containing
the following keys:

* album_title
* album_artist
* album_performer
* album_genre
* album_year
* album_cover
* is_compilation
* disc_number
* disc_total
* track_number
* track_total
* track_title
* track_artist
* track_performer
* track_genre
* track_year

The default string formats are chosen based on whether or not the album is a
compilation (affects directory and file names), and whether or not the album
spans two or more physical discs (affects file names only).

.. data:: flacmanager.FLAC_FOLDERS_TEMPLATE
   :annotation: = ["%(album_artist)s", "%(album_title)s"]

   A list of format strings for individual folder names that, joined, make
   up the *library_root*-relative directory path for a FLAC file.


.. data:: flacmanager.FLAC_FOLDERS_COMPILATION_TEMPLATE
   :annotation: = ["_COMPILATIONS_", "%(album_title)s"]

   A list of format strings for individual folder names that, joined, make
   up the *library_root*-relative directory path for a FLAC file that is
   part of a compilation.

.. autofunction:: flacmanager.generate_flac_dirname

.. data:: flacmanager.FLAC_FILENAME_TEMPLATE
   :annotation: = "%(track_number)02d %(track_title)s.flac"

   The format string for a FLAC filename.

.. data:: flacmanager.FLAC_FILENAME_COMPILATION_TEMPLATE
   :annotation: = "%(track_number)02d %(track_title)s (%(track_artist)s).flac"

   The format string for a FLAC filename that is part of a compilation.

.. data:: flacmanager.FLAC_FILENAME_DISCN_TEMPLATE
   :annotation: = "%(disc_number)02d-%(track_number)02d %(track_title)s.flac"

   The format string for a FLAC filename on an album of 2+ discs.

.. data:: flacmanager.FLAC_FILENAME_DISCN_COMPILATION_TEMPLATE
   :annotation: = "%(disc_number)02d-%(track_number)02d %(track_title)s (%(track_artist)s).flac"

   The format string for a FLAC filename that is part of a compilation
   spanning 2+ discs.

.. autofunction:: flacmanager.generate_flac_basename

.. data:: flacmanager.MP3_FOLDERS_TEMPLATE
   :annotation: = ["%(album_artist)s", "%(album_title)s"]

   A list of format strings for individual folder names that, joined, make
   up the *library_root*-relative directory path for an MP3 file.


.. data:: flacmanager.MP3_FOLDERS_COMPILATION_TEMPLATE
   :annotation: = ["_COMPILATIONS_", "%(album_title)s"]

   A list of format strings for individual folder names that, joined, make
   up the *library_root*-relative directory path for an MP3 file that is
   part of a compilation.

.. autofunction:: flacmanager.generate_mp3_dirname

.. data:: flacmanager.MP3_FILENAME_TEMPLATE
   :annotation: = "%(track_number)02d %(track_title)s.mp3"

   The format string for an MP3 filename.

.. data:: flacmanager.MP3_FILENAME_COMPILATION_TEMPLATE
   :annotation: = "%(track_number)02d %(track_title)s (%(track_artist)s).mp3"

   The format string for an MP3 filename that is part of a compilation.

.. data:: flacmanager.MP3_FILENAME_DISCN_TEMPLATE
   :annotation: = "%(disc_number)02d-%(track_number)02d %(track_title)s.mp3"

   The format string for an MP3 filename on an album of 2+ discs.

.. data:: flacmanager.MP3_FILENAME_DISCN_COMPILATION_TEMPLATE
   :annotation: = "%(disc_number)02d-%(track_number)02d %(track_title)s (%(track_artist)s).mp3"

   The format string for an MP3 filename that is part of a compilation
   spanning 2+ discs.

.. autofunction:: flacmanager.generate_mp3_basename

.. autoclass:: flacmanager.FLACEncoder

.. autofunction:: flacmanager.make_vorbis_comments

.. autofunction:: flacmanager.encode_flac

.. autoclass:: flacmanager.MP3Encoder

.. autofunction:: flacmanager.decode_wav

.. autofunction:: flacmanager.make_id3v2_tags

.. autofunction:: flacmanager.encode_mp3

.. autofunction:: flacmanager.get_lame_genres

