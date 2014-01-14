====================
Disc information API
====================

.. autofunction:: flacmanager.get_disc_info

.. autofunction:: flacmanager.read_disc_toc

.. data:: flacmanager.TOC
   :annotation: (first_track_number, last_track_number, track_offsets, leadout_track_offset)

   This named tuple represents a CD-DA disc table-of-contents (TOC), as read
   from a *.TOC.plist* file.

.. autoclass:: flacmanager.DiscCheck

