==============================
Audio tagging and encoding API
==============================

:Release: |release|

.. autofunction:: flacmanager.generate_flac_dirname
.. autofunction:: flacmanager.generate_flac_basename

.. autofunction:: flacmanager.generate_mp3_dirname
.. autofunction:: flacmanager.generate_mp3_basename

The directory and file names are configurable via the *flacmanager.ini*
file. Here are the relevant excerpts (default)::

   [Organize]
   library_root =
   library_subroot_trie_key = album_artist
   library_subroot_compilation_trie_key = album_title
   library_subroot_trie_level = 1
   album_folder = {album_artist}/{album_title}
   ndisc_album_folder = ${album_folder}
   compilation_album_folder = {album_title}
   ndisc_compilation_album_folder = ${compilation_album_folder}
   track_filename = {track_number:02d} {track_title}
   ndisc_track_filename = {album_discnumber:02d}-${track_filename}
   compilation_track_filename = ${track_filename} ({track_artist})
   ndisc_compilation_track_filename = {album_discnumber:02d}-${compilation_track_filename}
   use_xplatform_safe_names = yes

   [FLAC]
   library_root = ${Organize:library_root}/FLAC
   library_subroot_trie_key = ${Organize:library_subroot_trie_key}
   library_subroot_compilation_trie_key = ${Organize:library_subroot_compilation_trie_key}
   library_subroot_trie_level = ${Organize:library_subroot_trie_level}
   album_folder = ${Organize:album_folder}
   ndisc_album_folder = ${Organize:ndisc_album_folder}
   compilation_album_folder = ${Organize:compilation_album_folder}
   ndisc_compilation_album_folder = ${Organize:ndisc_compilation_album_folder}
   track_filename = ${Organize:track_filename}
   ndisc_track_filename = ${Organize:ndisc_track_filename}
   compilation_track_filename = ${Organize:compilation_track_filename}
   ndisc_compilation_track_filename = ${Organize:ndisc_compilation_track_filename}
   track_fileext = .flac
   use_xplatform_safe_names = ${Organize:use_xplatform_safe_names}

   [MP3]
   library_root = ${Organize:library_root}/MP3
   library_subroot_trie_key = ${Organize:library_subroot_trie_key}
   library_subroot_trie_level = ${Organize:library_subroot_trie_level}
   library_subroot_compilation_trie_key = ${Organize:library_subroot_compilation_trie_key}
   album_folder = ${Organize:album_folder}
   ndisc_album_folder = ${Organize:ndisc_album_folder}
   compilation_album_folder = ${Organize:compilation_album_folder}
   ndisc_compilation_album_folder = ${Organize:ndisc_compilation_album_folder}
   track_filename = ${Organize:track_filename}
   ndisc_track_filename = ${Organize:ndisc_track_filename}
   compilation_track_filename = ${Organize:compilation_track_filename}
   ndisc_compilation_track_filename = ${Organize:ndisc_compilation_track_filename}
   track_fileext = .mp3
   use_xplatform_safe_names = ${Organize:use_xplatform_safe_names}

.. autoclass:: flacmanager.TrackState
.. autodata:: flacmanager.TRACK_EXCLUDED
.. autodata:: flacmanager.TRACK_PENDING
.. autodata:: flacmanager.TRACK_ENCODING_FLAC
.. autodata:: flacmanager.TRACK_DECODING_WAV
.. autodata:: flacmanager.TRACK_ENCODING_MP3
.. autodata:: flacmanager.TRACK_REENCODING_MP3
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

Settings for the ``flac`` and ``lame`` encoders are configurable via the
*flacmanager.ini* file. Here are the relevant excerpts (default)::

   [FLAC]
   flac_encode_options = --force --keep-foreign-metadata --verify
   flac_decode_options = --force

   [MP3]
   lame_encode_options = --clipdetect -q 2 -V2 -b 224

.. autofunction:: flacmanager.get_lame_genres

