==============================
Audio tagging and encoding API
==============================

The ``FLAC_*_TEMPLATE`` and ``MP3_*_TEMPLATE`` string formats are used to
create the directory and file names for *.flac* and *.mp3* files, respectively.
These string formats take their substitution values from a dictionary of
metadata items containing the following keys:

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

.. autodata:: flacmanager.FLAC_FOLDERS_TEMPLATE
.. autodata:: flacmanager.FLAC_FOLDERS_COMPILATION_TEMPLATE
.. autofunction:: flacmanager.generate_flac_dirname

.. autodata:: flacmanager.FLAC_FILENAME_TEMPLATE
.. autodata:: flacmanager.FLAC_FILENAME_COMPILATION_TEMPLATE
.. autodata:: flacmanager.FLAC_FILENAME_DISCN_TEMPLATE
.. autodata:: flacmanager.FLAC_FILENAME_DISCN_COMPILATION_TEMPLATE
.. autofunction:: flacmanager.generate_flac_basename

.. autodata:: flacmanager.MP3_FOLDERS_TEMPLATE
.. autodata:: flacmanager.MP3_FOLDERS_COMPILATION_TEMPLATE
.. autofunction:: flacmanager.generate_mp3_dirname

.. autodata:: flacmanager.MP3_FILENAME_TEMPLATE
.. autodata:: flacmanager.MP3_FILENAME_COMPILATION_TEMPLATE
.. autodata:: flacmanager.MP3_FILENAME_DISCN_TEMPLATE
.. autodata:: flacmanager.MP3_FILENAME_DISCN_COMPILATION_TEMPLATE
.. autofunction:: flacmanager.generate_mp3_basename

.. autoclass:: flacmanager.TrackState
.. autodata:: flacmanager.TRACK_EXCLUDED
.. autodata:: flacmanager.TRACK_PENDING
.. autodata:: flacmanager.TRACK_ENCODING_FLAC
.. autodata:: flacmanager.TRACK_DECODING_WAV
.. autodata:: flacmanager.TRACK_ENCODING_MP3
.. autodata:: flacmanager.TRACK_FAILED
.. autodata:: flacmanager.TRACK_COMPLETE
.. autoclass:: flacmanager.TrackEncodingStatus

.. autoclass:: flacmanager.FLACEncoder
.. autofunction:: flacmanager.make_vorbis_comments
.. autofunction:: flacmanager.encode_flac

.. autoclass:: flacmanager.MP3Encoder
.. autofunction:: flacmanager.decode_wav
.. autofunction:: flacmanager.make_id3v2_tags
.. autofunction:: flacmanager.encode_mp3

.. autofunction:: flacmanager.get_lame_genres

