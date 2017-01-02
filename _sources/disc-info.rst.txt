====================
Disc information API
====================

:Release: |release|

.. autoclass:: flacmanager.DiscCheck

.. autofunction:: flacmanager.identify_cdda_device
.. autofunction:: flacmanager.identify_cdda_mount_point
.. autofunction:: flacmanager.read_disc_toc

.. class:: flacmanager.TOC

   This named tuple represents a CD-DA disc table-of-contents (TOC), as read
   from a *.TOC.plist* file.

   .. autoattribute:: flacmanager.TOC.first_track_number

   .. autoattribute:: flacmanager.TOC.last_track_number

   .. autoattribute:: flacmanager.TOC.track_offsets

   .. autoattribute:: flacmanager.TOC.leadout_track_offset

