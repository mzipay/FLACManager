#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

"""FLACManager is an audio metadata aggregator and FLAC+MP3
at-once tagger/encoder.

Audio metadata is aggregated from the Gracenote CDDB and
MusicBrainz services and presented to the user for acceptance,
selection and/or modification.

The file system folder and file names for ripped albums are
fully configurable via the flacmanager.ini configuration file.

Vorbis comments for FLAC files and ID3v2 tags for MP3 files are
also fully configurable.  In the default configuration, ID3v2
tags are compatible with both the iTunes application and the
Google Play Music service.  Custom Vorbis comments and ID3v2 tags
can also be defined on a per-album and/or per-track basis.

Immediately before tracks are encoded/tagged, FLACManager saves
all chosen metadata fields and custom Vorbis/ID3v2 tagging data
to a special file that is unique for that disc.  If the same disc
is inserted again, the saved information is restored in the UI
(including the chosen album art).  This provivdes for easy
recovery from failed encoding operations, or the ability to
re-encode/re-tag albums without needing to re-edit all metadata.

FLAC and MP3 encoding options are fully configurable via the
flacmanager.ini configuration file. By default, FLACManager
detects clipping in MP3s and will automatically re-encode with
scaled PCM data in order to eliminate clipping.

Please read the following articles before using FLACManager!

http://mzipay.github.io/FLACManager/prerequisites.html
http://mzipay.github.io/FLACManager/whats-new.html
http://mzipay.github.io/FLACManager/usage.html

"""

__author__ = "Matthew Zipay <mattz@ninthtest.info>"
__version__ = "0.8.1-beta"
__license__ = """\
FLAC Manager -- audio metadata aggregator and FLAC+MP3 encoder
http://ninthtest.info/flac-mp3-audio-manager/

Copyright (c) 2013-2017 Matthew Zipay. All rights reserved.

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE."""

from ast import literal_eval
import atexit
import cgi
from collections import namedtuple, OrderedDict
from configparser import ConfigParser, ExtendedInterpolation
from copy import deepcopy
import ctypes as C
import datetime
from functools import lru_cache, partial, total_ordering
from http.client import HTTPConnection, HTTPMessage, HTTPSConnection
import imghdr
from io import BytesIO, StringIO
import json
import logging
import os
import plistlib
import queue
import re
import ssl
import subprocess
import sys
from tempfile import mkstemp, TemporaryDirectory
import threading
import time
from tkinter import *
from tkinter.ttk import *
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
import tkinter.scrolledtext as scrolledtext
import tkinter.simpledialog as simpledialog
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

__all__ = [
    "TOC",
    "get_config",
    "save_config",
    "FLACManagerError",
    "FLACManager",
    "MetadataError",
    "MetadataCollector",
    "GracenoteCDDBMetadataCollector",
    "MusicBrainzMetadataCollector",
    "MetadataPersistence",
    "MetadataAggregator",
]

#: A custom tracing log level, lower in severity than
#: :attr:`logging.DEBUG`.
TRACE = 1
logging.addLevelName(TRACE, "TRACE")


class _TracingLogger(logging.getLoggerClass()):
    """A logger with tracing capability."""

    def call(self, *args, **kwargs):
        """Log entry into a callable with severity :attr:`TRACE`.

        :arg tuple args: the positional arguments to the callable
        :arg dict kwargs: the keyword arguments to the callable

        .. note::
           The positional and keyword arguments to this method are
           **not** interpreted as for :meth:`logging.Logger.log`; they
           should be argument-for-argument identical values as passed
           to the callable being traced.

        """
        if self.isEnabledFor(TRACE):
            self._log(TRACE, "CALL *%r **%r", (args, kwargs))

    def trace(self, msg, *args, **kwargs):
        """Log 'msg % args' with severity :attr:`TRACE`.

        Positional and keyword arguments are interpreted as for
        :meth:`logging.Logger.log`.

        """
        self.log(TRACE, msg, *args, **kwargs)

    def mark(self, marker="MARK"):
        """Log *marker* with severity :attr:`TRACE`."""
        self.log(TRACE, marker)

    def return_(self, value=None):
        """Log return from a callable with severity :attr:`TRACE`.

        :keyword value: the value returned from the callable

        """
        if self.isEnabledFor(TRACE):
            self._log(TRACE, "RETURN %r", (value,))


logging.setLoggerClass(_TracingLogger)

#: The module-level logger.
_log = logging.getLogger(__name__)


def logged(cls):
    """Decorate *cls* to provide a logger.

    :arg type cls: a class (type) object
    :return: *cls* with a provided ``__log`` member
    :rtype: :obj:`type`

    """
    setattr(
        cls, "_%s__log" % re.sub(r"^_+", "", cls.__name__),
        logging.getLogger(cls.__name__))

    return cls


#: The amount of time (in milliseconds) to wait before attempting
#: another call to any :meth:`queue.Queue.get_nowait` method.
QUEUE_GET_NOWAIT_AFTER = 625


def identify_cdda_device():
    """Locate the file system device for an inserted CD-DA.

    :return: the CD-DA file system device ("/dev/<device>")
    :rtype: :obj:`str`

    """
    # do not trace; called repeatedly by a the DiscCheck thread
    output = subprocess.check_output(
        ["diskutil", "list"], stderr=subprocess.STDOUT)
    output = output.decode(sys.getfilesystemencoding())

    is_cd_partition_scheme = False
    is_cd_da = False
    for line in StringIO(output):
        tokens = line.split()
        if tokens[0].startswith("/dev/"):
            device = tokens[0]
            continue

        if "CD_partition_scheme" in tokens:
            _log.debug("candidate %s: %s", device, line)
            is_cd_partition_scheme = True
            continue
        elif "CD_DA" in tokens:
            is_cd_da = True
            break

    if is_cd_partition_scheme and is_cd_da:
        return device


def identify_cdda_mount_point(device):
    """Locate the file system mount point for the CD-DA *device*.

    :arg str device: the CD-DA device ("/dev/<device>")
    :return: the *device* mount point
    :rtype: :obj:`str`

    """
    # do not trace; called repeatedly by a the DiscCheck thread
    output = subprocess.check_output(
        ["diskutil", "info", device], stderr=subprocess.STDOUT)
    output = output.decode(sys.getfilesystemencoding())

    for line in StringIO(output):
        match = re.search(r"\s+Mount Point:\s+(.*?)$", line)
        if match is not None:
            return match.group(1)


#: The number of seconds to wait between querying ``diskutil`` for the
#: inserted CD-DA device.
_CDDA_DEVICE_IDENT_WAIT = 0.5

#: The number of seconds to wait between querying ``diskutil`` for the
#: inserted CD-DA device's mount point.
_CDDA_MOUNT_POINT_IDENT_WAIT = 1.5

#: Used to pass data between a :class:`DiscCheck` thread and the main
#: thread.
_DISC_QUEUE = queue.Queue(1)


@logged
class DiscCheck(threading.Thread):
    """A thread that checks for the presence of a CD-DA disc."""

    def __init__(self):
        """``DiscCheck`` threads are daemonized so that they are killed
        automatically if the program exits.

        """
        self.__log.call()
        super().__init__(daemon=True)

    def run(self):
        """Poll for a mounted CD-DA disk device until one is found or an
        exception occurs.

        """
        self.__log.call()

        device = None
        mount_point = None
        try:
            while device is None:
                device = identify_cdda_device()
                time.sleep(_CDDA_DEVICE_IDENT_WAIT)
            self.__log.info("identified CD-DA device %s", device)

            while mount_point is None:
                # sleep first here to give the device time to mount
                time.sleep(_CDDA_MOUNT_POINT_IDENT_WAIT)
                mount_point = identify_cdda_mount_point(device)
            self.__log.info("identified CD-DA mount point %s", mount_point)

            disc_info = (device, mount_point)
        except Exception as e:
            self.__log.error("enqueueing %r", e)
            _DISC_QUEUE.put(e)
        else:
            self.__log.info("enqueueing %r", disc_info)
            _DISC_QUEUE.put(disc_info)


#: Represents a disc table-of-contents (TOC), as read from a
#: *.TOC.plist* file.
TOC = namedtuple(
    "TOC",
    ["first_track_number", "last_track_number", "track_offsets",
        "leadout_track_offset"])


def read_disc_toc(mountpoint):
    """Return the :obj:`TOC` for the currently mounted disc.

    :arg str mountpoint: the mount point of an inserted CD-DA disc
    :return: a populated TOC for the inserted CD-DA disc
    :rtype: :obj:`TOC`

    """
    _log.call(mountpoint)

    toc_plist_filename = os.path.join(mountpoint, ".TOC.plist")
    toc_plist = plistlib.readPlist(toc_plist_filename)

    first_track_number = None
    last_track_number = None
    track_offsets = []
    leadout_track_offset = None
    for session in toc_plist["Sessions"]:
        # Session Type 0 is CD-DA
        if session["Session Type"] == 0:
            first_track_number = session["First Track"]
            last_track_number = session["Last Track"]
            leadout_track_offset = session["Leadout Block"]
            for track in session["Track Array"]:
                track_offsets.append(track["Start Block"])
            # don't need to process any more sessions
            break

    toc = TOC(
        first_track_number, last_track_number, tuple(track_offsets),
        leadout_track_offset)

    _log.return_(toc)
    return toc


#: The global :class:`configparser.ConfigParser` object.
_config = None

#: Used to synchronize access to the global configuration object.
_CONFIG_LOCK = threading.RLock()


def get_config():
    """Return the configuration settings.

    :return: settings read from *flacmanager.ini*
    :rtype: :class:`configparser.ConfigParser`

    The configuration is initialized with default/empty values and saved
    to disk if it does not exist.

    """
    _log.call()

    global _config
    with _CONFIG_LOCK:
        if _config is None:
            _log.info("initializing configuration")
            _config = ConfigParser(interpolation=ExtendedInterpolation())
            _config.optionxform = lambda option: option # preserve casing

            if (_config.read("flacmanager.ini") != ["flacmanager.ini"]
                    or "FLACManager" not in _config
                    or _config["FLACManager"].get("__version__") !=
                        __version__):
                _log.warning(
                    "flacmanager.ini is missing or outdated; "
                        "updating to version %s",
                    __version__)

                fm = {}
                if "FLACManager" in _config:
                    fm.update(_config["FLACManager"])

                # always make sure this is accurate
                _config["FLACManager"] = OrderedDict(__version__=__version__)

                for (key, default_value) in [
                        ("title", "FLACManager ${__version__}"),
                        ]:
                    _config["FLACManager"].setdefault(key, default_value)

                if "UI" not in _config:
                    _config["UI"] = OrderedDict()
                _config["UI"].setdefault("minwidth", fm.pop("minwidth", "1024"))
                _config["UI"].setdefault("minheight", fm.pop("minheight", "768"))
                for (key, default_value) in [
                        #("minwidth", "1024"),
                        #("minheight", "768"),
                        ("padx", "7"),
                        ("pady", "5"),
                        ("disable_editing_excluded_tracks", "no"),
                        ("encoding_max_visible_tracks", "29"),
                        ]:
                    _config["UI"].setdefault(key, default_value)

                if "Logging" not in _config:
                    _config["Logging"] = OrderedDict()
                for (key, default_value) in [
                        ("level", "WARNING"),
                        ("filename", "flacmanager.log"),
                        ("filemode", 'w'),
                        ("format", 
                            "%(asctime)s %(levelname)s [%(threadName)s "
                            "%(name)s.%(funcName)s] %(message)s"),
                        ]:
                    _config["Logging"].setdefault(key, default_value)
                        

                if "HTTP" not in _config:
                    _config["HTTP"] = OrderedDict()
                for (key, default_value) in [
                        ("debuglevel", '0'),
                        ("timeout", "5.0"),
                        ]:
                    _config["HTTP"].setdefault(key, default_value)

                if "Gracenote" not in _config:
                    _config["Gracenote"] = OrderedDict()
                for (key, default_value) in [
                        ("client_id", ""),
                        ("user_id", ""),
                        ]:
                    _config["Gracenote"].setdefault(key, default_value)

                if "MusicBrainz" not in _config:
                    _config["MusicBrainz"] = OrderedDict()
                for (key, default_value) in [
                        ("contact_url_or_email", ""),
                        ("libdiscid_location", ""),
                        ]:
                    _config["MusicBrainz"].setdefault(key, default_value)

                # TODO: add Discogs for metadata aggregation
                '''
                if "Discogs" not in _config:
                    _config["Discogs"] = OrderedDict()
                for (key, default_value) in [
                        ]:
                    _config["Discogs"].setdefault(key, default_value)
                '''

                if "Organize" not in _config:
                    _config["Organize"] = OrderedDict()
                for (key, default_value) in [
                        ("library_root", ""),
                        ("library_subroot_trie_key", "album_artist"),
                        ("library_subroot_compilation_trie_key",
                            "album_title"),
                        ("library_subroot_trie_level", '1'),
                        ("trie_ignore_leading_article", "a an the"),
                        ("album_folder", "{album_artist}/{album_title}"),
                        ("ndisc_album_folder", "${album_folder}"),
                        ("compilation_album_folder", "{album_title}"),
                        ("ndisc_compilation_album_folder",
                            "${compilation_album_folder}"),
                        ("track_filename", "{track_number:02d} {track_title}"),
                        ("ndisc_track_filename",
                            "{album_discnumber:02d}-${track_filename}"),
                        ("compilation_track_filename",
                            "${track_filename} ({track_artist})"),
                        ("ndisc_compilation_track_filename",
                            "{album_discnumber:02d}-${compilation_track_filename}"),
                        ("use_xplatform_safe_names", "yes"),
                        ]:
                    _config["Organize"].setdefault(key, default_value)

                if "FLAC" not in _config:
                    _config["FLAC"] = OrderedDict()
                for (key, default_value) in [
                        ("library_root", "${Organize:library_root}/FLAC"),
                        ("library_subroot_trie_key",
                            "${Organize:library_subroot_trie_key}"),
                        ("library_subroot_compilation_trie_key",
                            "${Organize:library_subroot_compilation_trie_key}"),
                        ("library_subroot_trie_level",
                            "${Organize:library_subroot_trie_level}"),
                        ("trie_ignore_leading_article",
                            "${Organize:trie_ignore_leading_article}"),
                        ("album_folder", "${Organize:album_folder}"),
                        ("ndisc_album_folder",
                            "${Organize:ndisc_album_folder}"),
                        ("compilation_album_folder",
                            "${Organize:compilation_album_folder}"),
                        ("ndisc_compilation_album_folder",
                            "${Organize:ndisc_compilation_album_folder}"),
                        ("track_filename", "${Organize:track_filename}"),
                        ("ndisc_track_filename",
                            "${Organize:ndisc_track_filename}"),
                        ("compilation_track_filename",
                            "${Organize:compilation_track_filename}"),
                        ("ndisc_compilation_track_filename",
                            "${Organize:ndisc_compilation_track_filename}"),
                        ("track_fileext", ".flac"),
                        ("use_xplatform_safe_names",
                            "${Organize:use_xplatform_safe_names}"),
                        ("flac_encode_options",
                            "--force --keep-foreign-metadata --verify"),
                        ("flac_decode_options", "--force"),
                        ]:
                    _config["FLAC"].setdefault(key, default_value)

                if "Vorbis" not in _config:
                    _config["Vorbis"] = OrderedDict()
                for (key, default_value) in [
                        ("ALBUM", "album_title"),
                        ("ALBUMARTIST", "album_artist"),
                        ("ORGANIZATION", "album_label"),
                        ("LABEL", "${ORGANIZATION}"),
                        ("DISCNUMBER", "{album_discnumber:d}"),
                        ("DISCTOTAL", "{album_disctotal:d}"),
                        ("TRACKNUMBER", "{track_number:d}"),
                        ("TRACKTOTAL", "{album_tracktotal:d}"),
                        ("TITLE", "track_title"),
                        ("ARTIST", "track_artist"),
                        ("GENRE", "track_genre"),
                        ("DATE", "track_year"),
                        ("COMPILATION", "{album_compilation:d}"),
                        ]:
                    _config["Vorbis"].setdefault(key, default_value)

                if "MP3" not in _config:
                    _config["MP3"] = OrderedDict()
                for (key, default_value) in [
                        ("library_root", "${Organize:library_root}/MP3"),
                        ("library_subroot_trie_key",
                            "${Organize:library_subroot_trie_key}"),
                        ("library_subroot_compilation_trie_key",
                            "${Organize:library_subroot_compilation_trie_key}"),
                        ("library_subroot_trie_level",
                            "${Organize:library_subroot_trie_level}"),
                        ("trie_ignore_leading_article",
                            "${Organize:trie_ignore_leading_article}"),
                        ("album_folder", "${Organize:album_folder}"),
                        ("ndisc_album_folder",
                            "${Organize:ndisc_album_folder}"),
                        ("compilation_album_folder",
                            "${Organize:compilation_album_folder}"),
                        ("ndisc_compilation_album_folder",
                            "${Organize:ndisc_compilation_album_folder}"),
                        ("track_filename", "${Organize:track_filename}"),
                        ("ndisc_track_filename",
                            "${Organize:ndisc_track_filename}"),
                        ("compilation_track_filename",
                            "${Organize:compilation_track_filename}"),
                        ("ndisc_compilation_track_filename",
                            "${Organize:ndisc_compilation_track_filename}"),
                        ("track_fileext", ".mp3"),
                        ("use_xplatform_safe_names",
                            "${Organize:use_xplatform_safe_names}"),
                        ("lame_encode_options",
                            "--clipdetect -q 2 -V2 -b 224"),
                        ]:
                    _config["MP3"].setdefault(key, default_value)

                if "ID3v2" not in _config:
                    _config["ID3v2"] = OrderedDict()
                for (key, default_value) in [
                        ("TALB", "album_title"),
                        ("TPE2", "album_artist"),
                        ("TPUB", "album_label"),
                        ("TPOS", "{album_discnumber:d}/{album_disctotal:d}"),
                        ("TRCK", "{track_number:d}/{album_tracktotal:d}"),
                        ("TIT2", "track_title"),
                        ("TIT1", "${TPE1}"),
                        ("TPE1", "track_artist"),
                        ("TCON", "track_genre"),
                        ("TYER", "track_year"),
                        ("TDRC", "${TYER}"),
                        ("TCMP", "{album_compilation:d}"),
                        ]:
                    _config["ID3v2"].setdefault(key, default_value)

                with open("flacmanager.ini", 'w') as f:
                    _config.write(f)

        _log.return_(_config)
        return _config


def save_config():
    """Write the configuration settings to an INI-style file."""
    _log.call()

    with _CONFIG_LOCK:
        config = get_config()

        with open("flacmanager.ini", 'w') as f:
            config.write(f)


def make_tempfile(suffix=".tmp", prefix="fm"):
    """Create a temporary file.

    :keyword str suffix: the default file extenstion
    :keyword str prefix: prepended to the beginning of the filename
    :return: the temporary file name
    :rtype: :obj:`str`

    The temporary file will be deleted automatically when the program
    exits.

    """
    (fd, filename) = mkstemp(suffix=suffix, prefix=prefix)
    # close the file descriptor; it isn't inherited by child processes
    os.close(fd)
    # clean up the temp file when FLACManager exits
    atexit.register(os.unlink, filename)
    _log.debug("created temp file %s", filename)
    return filename


class FLACManagerError(Exception):
    """The type of exception raised when FLACManager operations fail."""

    def __init__(self, message, context_hint=None, cause=None):
        """
        :arg str message: error message for logging or display
        :keyword context_hint: describes the error context
        :keyword Exception cause: the exception that caused this error

        The optional *context_hint* is not part of the message, and may
        take any type or form. Exception handlers that catch
        ``FLACManagerError`` may choose to do something with the context
        hint, or may ignore it.

        The optional *cause* is the (caught) exception that caused this
        ``FLACManagerError``.

        """
        super().__init__(message)
        self.context_hint = context_hint
        self.cause = cause


#: The standard amount of X-axis padding for the FLACManager UI.
_PADX = 7

#: The standard amount of Y-axis padding for the FLACManager UI.
_PADY = 5


@logged
class FLACManager(Tk):
    """The FLACManager GUI application."""

    #: Any HTTP(S) request issued by FLACManager uses this value for the
    #: HTTP User-Agent header value.
    USER_AGENT = "FLACManager/{v} Python/{vi[0]:d}.{vi[1]:d}.{vi[2]:d}".format(
        v=__version__, vi=sys.version_info)

    def __init__(self):
        self.__log.call()
        super().__init__()

        config = get_config()

        self.title(config["FLACManager"]["title"])
        self.minsize(
            config["UI"].getint("minwidth"), config["UI"].getint("minheight"))

        self.config(menu=_FMMenu(self, name="menubar"))

        self._disc_frame = _FMDiscFrame(self, name="disc_frame", text="Disc")
        self._status_frame = _FMStatusFrame(self, name="status_frame")
        self._editor_frame = _FMEditorFrame(self, name="editor_frame")
        self._encoding_status_frame = _FMEncodingStatusFrame(
            self, name="encoding_status_frame", text="Encoding status")

        self.reset()

    @property
    def disk(self):
        """The disk device node for the inserted CD-DA disc."""
        return self.__disk

    @property
    def mountpoint(self):
        """The file system mount point for the inserted CD-DA disc."""
        return self.__mountpoint

    @property
    def toc(self):
        """The CD-DA disc's :obj:`TOC` (table-of-contents)."""
        return self.__toc

    def reset(self):
        """(Re)Initialize the FLACManager GUI."""
        self._remove()

        self.__disk = None
        self.__mountpoint = None
        self.__toc = None

        # not repacked until user initiates rip+tag
        self._encoding_status_frame.reset()

        # not repacked until metadata for an inserted disc has been aggregated
        self._editor_frame.reset()

        self._disc_frame.reset()
        self._disc_frame.pack(anchor=N, fill=X, padx=_PADX, pady=_PADY)

        self._status_frame.reset()
        self._status_frame.pack(anchor=N, fill=X, padx=_PADX, pady=_PADY)

        if self.has_required_config:
            self.check_for_disc()
        else:
            self._status_frame.required_configuration_missing()

        self.update()

    def _remove(self):
        self._encoding_status_frame.pack_forget()
        self._editor_frame.pack_forget()
        self._status_frame.pack_forget()
        self._disc_frame.pack_forget()

        self._persistence = None

    @property
    def has_required_config(self):
        """Whether or not required configuration settings have been
        specified.

        """
        config = get_config()

        # the following options MUST be set by the user before FLACManager can
        # be used
        return (
            config["Organize"].get("library_root")
            and config["Gracenote"].get("client_id")
            and config["MusicBrainz"].get("contact_url_or_email")
            and config["MusicBrainz"].get("libdiscid_location")
        )

    def edit_required_config(self):
        """Open a *flacmanager.ini* editor to allow the user to provide
        required configuration settings.

        """
        EditRequiredConfigurationDialog(
            self, title="Edit flacmanager.ini (required settings)")

        if self.has_required_config:
            self.reset()
            self.check_for_disc()

    def check_for_disc(self):
        """Spawn the :class:`DiscCheck` thread."""
        self.__log.call()

        self._disc_frame.reset()
        DiscCheck().start()
        self._update_disc_info()

    def _update_disc_info(self):
        """Update the UI if a CD-DA disc is present.

        If a disc is **not** present, set a UI timer to check again.

        """
        # do not trace; called indefinitely until a disc is found
        try:
            disc_info = _DISC_QUEUE.get_nowait()
        except queue.Empty:
            self.after(QUEUE_GET_NOWAIT_AFTER, self._update_disc_info)
        else:
            _DISC_QUEUE.task_done()

            if isinstance(disc_info, Exception):
                self.__log.error("dequeued %r", disc_info)
                show_exception_dialog(disc_info)
                self._disc_frame.disc_check_failed()
                return

            self.__log.debug("dequeued %r", disc_info)
            (self.__disk, self.__mountpoint) = disc_info
            self._disc_frame.disc_mounted(self.mountpoint)

            self.__toc = read_disc_toc(self.mountpoint)

            self.aggregate_metadata()

    def aggregate_metadata(self):
        """Spawn the :class:`MetadataAggregator` thread."""
        self.__log.call()

        self._status_frame.aggregating_metadata()
        self._status_frame.pack(anchor=N, fill=X, padx=_PADX, pady=_PADY)

        try:
            MetadataAggregator(self.toc).start()
        except Exception as e:
            self.__log.exception("failed to start metadata aggregator")
            show_exception_dialog(e)

            self._status_frame.aggregation_failed()
        else:
            self._update_aggregated_metadata()

    def _update_aggregated_metadata(self):
        """Update the UI if aggregated metadata is ready.

        If aggregated metadata is **not** ready, set a UI timer to check
        again.

        """
        # don't log entry into this method - it calls itself recursively until
        # the aggregated metadata is ready
        try:
            aggregator = _AGGREGATOR_QUEUE.get_nowait()
        except queue.Empty:
            self.after(
                QUEUE_GET_NOWAIT_AFTER, self._update_aggregated_metadata)
        else:
            self.__log.debug("dequeued %r", aggregator)
            _AGGREGATOR_QUEUE.task_done()

            self._persistence = aggregator.persistence
            # metadata may be "partial" if an error occurred while collecting
            # or aggregating, but initialize the editor frame regardless
            self._editor_frame.metadata_ready_for_editing(aggregator.metadata)

            if not aggregator.exceptions:
                self._edit_metadata()
            else:
                show_exception_dialog(aggregator.exceptions[0])
                self._status_frame.aggregation_failed()

    def _edit_metadata(self):
        """Display the metadata editor."""
        self.__log.call()

        self._status_frame.pack_forget()
        self._editor_frame.pack(anchor=N, fill=BOTH, padx=_PADX, pady=_PADY)
        self._disc_frame.rip_and_tag_ready()

    def rip_and_tag(self):
        """Create tagged FLAC and MP3 files of all included tracks."""
        self.__log.call()

        self._disc_frame.ripping_and_tagging()

        # issues/1
        self.persist_metadata_snapshot(showinfo=False)

        per_track_metadata = self._editor_frame.flattened_metadata
        try:
            encoder = self._prepare_encoder(per_track_metadata)
        except Exception as e:
            self.__log.exception("failed to initialize the encoder")
            show_exception_dialog(e)
            self._disc_frame.rip_and_tag_failed()
        else:
            self._encoding_status_frame.ready_to_encode(per_track_metadata)

            # at this point, the encoder is ready and the status frame has been
            # initialized for display
            self._editor_frame.reset()
            self._encoding_status_frame.pack(
                anchor=N, fill=X, expand=YES, padx=_PADX, pady=_PADY)

            # rock and roll
            encoder.start()

            self.__log.info("encoding has started; monitoring progress...")
            self._encoding_status_frame.encoding_in_progress()

    def persist_metadata_snapshot(self, showinfo=True):
        """Serialize the current metadata field values to JSON.

        :keyword bool showinfo:
           whether or not to display a messagebox with the persisted
           metadata file path

        """
        self.__log.call()
        self._persistence.store(self._editor_frame.metadata_snapshot)

        if showinfo:
            messagebox.showinfo(
                "Metadata snapshot saved", self._persistence.metadata_filename)

    def _prepare_encoder(self, per_track_metadata):
        """Initialize a :class:`FLACEncoder` with instructions to encode
        each **included** track from *per_track_metadata*.

        :arg list per_track_metadata: metadata mappings for each track
        :return:
           an initialized :class:`FLACEncoder`, ready to execute the
           encoding instructions in a separate thread

        """
        self.__log.call(per_track_metadata)

        disc_filenames = [
            name for name in os.listdir(self.mountpoint)
            if not name.startswith('.')
                and os.path.splitext(name)[1] in [
                    ".aiff",
                    ".aif",
                    ".aifc",
                    ".cdda",
                    ".cda"]]

        # sanity checks
        if len(disc_filenames) != len(self.toc.track_offsets):
            raise FLACManagerError(
                ("Disc TOC contains %d tracks, but %d CD-DA files were found "
                        "under %s") % (
                    len(self.toc.track_offsets), len(disc_filenames),
                    self.mountpoint),
                context_hint="FLAC+MP3 encoding")
        elif len(disc_filenames) != len(per_track_metadata):
            raise FLACManagerError(
                ("Found %d CD-DA files to encode, but there are %d metadata "
                    "mappings") % (len(disc_filenames), len(per_track_metadata)),
                context_hint="FLAC+MP3 encoding")

        config = get_config()

        flac_library_root = config["FLAC"]["library_root"]
        try:
            flac_library_root = resolve_path(flac_library_root)
        except Exception as e:
            raise FLACManagerError(
                "Cannot use FLAC library root %r: %s" % (flac_library_root, e),
                context_hint="FLAC encoding", cause=e)

        mp3_library_root = config["MP3"]["library_root"]
        try:
            mp3_library_root = resolve_path(mp3_library_root)
        except Exception as e:
            raise FLACManagerError(
                "Cannot use MP3 library root %r: %s" % (mp3_library_root, e),
                context_hint="MP3 encoding", cause=e)

        encoder = FLACEncoder()
        for (i, track_metadata) in enumerate(per_track_metadata):
            if not track_metadata["track_include"]:
                continue

            cdda_filename = os.path.join(self.mountpoint, disc_filenames[i])

            flac_dirname = generate_flac_dirname(
                flac_library_root, track_metadata)
            flac_basename = generate_flac_basename(track_metadata)
            flac_filename = os.path.join(flac_dirname, flac_basename)

            mp3_dirname = generate_mp3_dirname(
                mp3_library_root, track_metadata)
            mp3_basename = generate_mp3_basename(track_metadata)
            mp3_filename = os.path.join(mp3_dirname, mp3_basename)

            encoder.add_instruction(
                i, cdda_filename, flac_filename, mp3_filename, track_metadata)

            self.__log.info(
                "prepared encoding instruction:\n%s\n-> %s\n-> %s",
                cdda_filename, flac_filename, mp3_filename)

        self.__log.return_(encoder)
        return encoder

    def eject_disc(self):
        """Eject the current CD-DA disc and update the UI."""
        self.__log.call()

        status = subprocess.call(
            ["diskutil", "eject", self.disk],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if status == 0:
            self.__log.info(
                "ejected %s mounted at %s", self.disk, self.mountpoint)
            # resetting will automatically spawn a new DiscCheck thread
            self.reset()
        else:
            self.__log.error(
                "unable to eject %s mounted at %s", self.disk, self.mountpoint)
            messagebox.showerror(
                title="Disk eject failure",
                message="Unable to eject %s" % self.mountpoint)

    def edit_aggregation_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditAggregationConfigurationDialog(
            self, title="Edit flacmanager.ini (metadata aggregation)")

    def edit_organization_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditOrganizationConfigurationDialog(
            self, title="Edit flacmanager.ini (default folder and file names)")

    def edit_flac_encoding_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditFLACEncodingConfigurationDialog(
            self, title="Edit flacmanager.ini (FLAC encoding)")

    def edit_vorbis_comments_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditVorbisCommentsConfigurationDialog(
            self, title="Edit flacmanager.ini (default FLAC Vorbis comments)")

    def edit_flac_organization_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditFLACOrganizationConfigurationDialog(
            self, title="Edit flacmanager.ini (FLAC folder and file names)")

    def edit_mp3_encoding_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditMP3EncodingConfigurationDialog(
            self, title="Edit flacmanager.ini (MP3 encoding)")

    def edit_id3v2_tags_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditID3v2TagsConfigurationDialog(
            self, title="Edit flacmanager.ini (default MP3 ID3v2 tags)")

    def edit_mp3_organization_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditMP3OrganizationConfigurationDialog(
            self, title="Edit flacmanager.ini (MP3 folder and file names)")

    def edit_ui_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditUserInterfaceConfigurationDialog(
            self, title="Edit flacmanager.ini (UI)")

    def edit_logging_config(self):
        """Open the configuration editor dialog."""
        self.__log.call()
        EditLoggingConfigurationDialog(
            self, title="Edit flacmanager.ini (logging/debug)")

    def show_about(self):
        """Open the application description dialog."""
        self.__log.call()
        _TextDialog(self, __doc__, title="About %s" % self.title())

    def show_prerequisites(self):
        """Open the prerequisites information dialog."""
        self.__log.call()
        _TextDialog(
            self, _PREREQUISITES_TEXT, title="%s prerequisites" % self.title())

    def show_license(self):
        """Open the copyright/license dialog."""
        self.__log.call()
        _TextDialog(
            self, __license__, title="%s copyright and license" % self.title())

    def exit(self):
        """Quit the FLACManager application."""
        self.withdraw()
        self.destroy()
        self.quit()


@logged
class _FMMenu(Menu):
    """The FLACManager application menu bar."""

    def __init__(self, *args, **options):
        """
        :arg tuple args: positional arguments to initialize the menu
        :arg dict options: ``config`` options to initialize the menu

        """
        self.__log.call(*args, **options)
        super().__init__(*args, **options)

        fm = self.master

        file_menu = Menu(self, name="file_menu", tearoff=NO)
        # only enabled while the editor frame is packed
        file_menu.add_command(
            label="Save metadata", command=fm.persist_metadata_snapshot,
            state=DISABLED)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=fm.exit)
        self.add_cascade(label="File", menu=file_menu)

        edit_menu = Menu(self, name="edit_menu", tearoff=YES)
        edit_menu.add_command(
            label="Configure metadata aggregation",
            command=fm.edit_aggregation_config)
        edit_menu.add_command(
            label="Configure default folder and file names",
            command=fm.edit_organization_config)

        edit_menu.add_separator()

        flac_menu = Menu(edit_menu, name="flac_menu", tearoff=NO)
        flac_menu.add_command(
            label="FLAC encoding options",
            command=fm.edit_flac_encoding_config)
        flac_menu.add_command(
            label="FLAC Vorbis comments",
            command=fm.edit_vorbis_comments_config)
        flac_menu.add_command(
            label="FLAC folder and file names",
            command=fm.edit_flac_organization_config)
        edit_menu.add_cascade(label="Configure FLAC", menu=flac_menu)

        mp3_menu = Menu(edit_menu, name="mp3_menu", tearoff=NO)
        mp3_menu.add_command(
            label="MP3 encoding options",
            command=fm.edit_mp3_encoding_config)
        mp3_menu.add_command(
            label="MP3 ID3v2 tags", command=fm.edit_id3v2_tags_config)
        mp3_menu.add_command(
            label="MP3 folder and file names",
            command=fm.edit_mp3_organization_config)
        edit_menu.add_cascade(label="Configure MP3", menu=mp3_menu)

        edit_menu.add_separator()

        edit_menu.add_command(
            label="Configure UI", command=fm.edit_ui_config)
        self.add_cascade(label="Edit", menu=edit_menu)

        edit_menu.add_command(
            label="Configure logging", command=fm.edit_logging_config)
        self.add_cascade(label="Edit", menu=edit_menu)

        help_menu = Menu(self, name="help_menu", tearoff=NO)
        help_menu.add_command(label="About", command=fm.show_about)
        help_menu.add_command(
            label="Prequisites", command=fm.show_prerequisites)
        help_menu.add_command(label="License", command=fm.show_license)
        self.add_cascade(label="Help", menu=help_menu)


@logged
class _FMDiscFrame(LabelFrame):
    """The disc status/operation frame for FLACManager."""

    def __init__(self, *args, **options):
        """
        :arg tuple args: positional arguments to initialize the frame
        :arg dict options: ``config`` options to initialize the frame

        All widgets for this frame are initialized, but layout is
        deferred until methods are called to transition between states.

        """
        self.__log.call(*args, **options)
        super().__init__(*args, **options)

        fm = self.master

        self._disc_eject_button = Button(
            self, name="disc_eject_button", text="Eject",
            command=fm.eject_disc)

        self._disc_status_label = Label(self, name="disc_status_label")

        self._retry_disc_check_button = Button(
            self, name="retry_disc_check_button", text="Retry disc check",
            command=fm.check_for_disc)

        self._rip_and_tag_button = _styled(
            Button(
                self, name="rip_and_tag_button", text="Rip and Tag",
                command=fm.rip_and_tag),
            foreground="Dark Green", font="-weight bold")

        self.grid_columnconfigure(1, weight=1)

    def _set_status_message(self, value, fg="Black"):
        """Set the disc status label text and color."""
        self.__log.call(value, fg=fg)
        self._disc_status_label.config(text=value)
        _styled(self._disc_status_label, foreground=fg)

    def disc_mounted(self, mountpoint):
        """Show the disk mointpoint and a button to eject the disc.

        :arg str mountpoint: where the CD-DA disc is mounted

        """
        self.__log.call(mountpoint)

        self._remove()

        self._disc_eject_button.config(state=NORMAL)
        self._disc_eject_button.grid(
            row=0, column=0, sticky=W, padx=_PADX, pady=_PADY)

        self._set_status_message(mountpoint)
        self._disc_status_label.grid(
            row=0, column=1, sticky=W, padx=_PADX, pady=_PADY)

    def disc_check_failed(self):
        """Alert the user that the disc check failed and provide a
        button to retry.

        """
        self.__log.call()

        self.reset()

        self._retry_disc_check_button.grid(
            row=0, column=2, sticky=E, padx=_PADX, pady=_PADY)

    def rip_and_tag_ready(self):
        """Provide a button to begin ripping and tagging the tracks."""
        self.__log.call()

        self._rip_and_tag_button.grid(
            row=0, column=2, sticky=E, padx=_PADX, pady=_PADY)
        self._rip_and_tag_button.config(state=NORMAL)

    def ripping_and_tagging(self):
        """Change the state of the disc controls while tracks are being
        ripped and tagged.

        """
        self.__log.call()

        self._disc_eject_button.config(state=DISABLED)
        self._rip_and_tag_button.config(state=DISABLED)

    def rip_and_tag_failed(self):
        """Restore the state of the disc controls after an encoding
        failure.

        """
        self.__log.call()

        self._disc_eject_button.config(state=NORMAL)
        self._rip_and_tag_button.config(state=NORMAL)

    def rip_and_tag_finished(self):
        """Change the state of the disc controls to reflect that a disc
        has been ripped and tagged.

        """
        self.__log.call()

        self._disc_eject_button.config(state=NORMAL)
        self._rip_and_tag_button.grid_remove()

    def reset(self):
        """Populate the disc status/operation frame widgets in their
        default/initial states.

        """
        self.__log.call()

        self._remove()

        self._disc_eject_button.config(state=DISABLED)

        self._set_status_message("Waiting for a disc to be inserted\u2026")
        self._disc_status_label.grid(
            row=0, column=1, sticky=W, padx=_PADX, pady=_PADY)

    def _remove(self):
        """Remove all widgets from the current layout."""
        self.__log.call()

        self._disc_eject_button.grid_remove()
        self._disc_status_label.grid_remove()
        self._retry_disc_check_button.grid_remove()
        self._rip_and_tag_button.grid_remove()


@logged
class _FMStatusFrame(Frame):
    """The application status frame for FLACManager."""

    def __init__(self, *args, **options):
        """
        :arg tuple args: positional arguments to initialize the frame
        :arg dict options: ``config`` options to initialize the frame

        All widgets for this frame are initialized, but layout is
        deferred until methods are called to transition between states.

        """
        self.__log.call(*args, **options)
        super().__init__(*args, **options)

        fm = self.master

        self._status_label = _styled(
            Label(self, name="status_label"), font="-weight bold")

        self._retry_aggregation_button = Button(
            self, name="retry_aggregation_button",
            text="Retry metadata aggregation",
            command=fm.aggregate_metadata)

        self._edit_asis_button = Button(
            self, name="edit_asis_button", text="Edit metadata as-is",
            command=fm._edit_metadata)

        self._open_req_config_editor_button = Button(
            self, name="open_req_config_editor_button",
            text="Edit flacmanager.ini",
            command=fm.edit_required_config)

        for r in range(2):
            self.grid_rowconfigure(r, weight=1)
        for c in range(2):
            self.grid_columnconfigure(c, weight=1)

    def _set_status_message(self, value, fg="Black"):
        """Set the status label text and color."""
        self.__log.call(value, fg=fg)
        self._status_label.config(text=value)
        _styled(self._status_label, foreground=fg)

    def _show_metadata_status_buttons(self):
        """Display the buttons that allow the user to choose an action
        after metadata aggregation has failed.

        """
        self.__log.call()

        self._retry_aggregation_button.grid(
            row=1, column=0, padx=_PADX, pady=_PADY, sticky=NE)
        self._edit_asis_button.grid(
            row=1, column=1, padx=_PADX, pady=_PADY, sticky=NW)

    def _hide_metadata_status_buttons(self):
        """Remove the buttons for handling metadata aggregation
        failures.

        """
        self.__log.call()

        self._retry_aggregation_button.grid_remove()
        self._edit_asis_button.grid_remove()

    def required_configuration_missing(self):
        """Let the user know that required configuration settings are
        not present, and provide a button to open an editor.

        """
        self.__log.call()

        self._remove()

        self._set_status_message(
            "Required configuration is missing!", fg="Red")
        self._status_label.grid(
            row=0, column=0, columnspan=2, padx=_PADX, pady=_PADY)

        self._open_req_config_editor_button.grid(
            row=1, column=0, columnspan=2, padx=_PADX, pady=_PADY)

    def aggregating_metadata(self):
        """Change this frame to reflect that metadata is being
        aggregated.

        """
        self.__log.call()

        self._remove()
        self._set_status_message("Aggregating metadata\u2026", fg="Grey")
        self._status_label.grid(
            row=0, column=0, columnspan=2, padx=_PADX, pady=_PADY)

    def aggregation_failed(self):
        """Message the user that metadata aggregation has failed and
        provide options for a next action.

        """
        self.__log.call()

        self._set_status_message(
            "Aggregation failed. Metadata may be missing or incomplete.",
            fg="Red")
        self._show_metadata_status_buttons()

    def reset(self):
        """Populate the status frame widgets in their default/initial
        states.

        """
        self.__log.call()

        self._remove()

        self._set_status_message("Please insert a disc.", fg="Grey")
        self._status_label.grid(
            row=0, column=0, columnspan=2, padx=_PADX, pady=_PADY)

    def _remove(self):
        """Remove all widgets from the current layout."""
        self.__log.call()

        self._status_label.grid_remove()
        self._hide_metadata_status_buttons()
        self._open_req_config_editor_button.grid_remove()


@logged
class _FMEditorFrame(Frame):
    """The metadata editing frame for FLACManager."""

    def __init__(self, *args, **options):
        """
        :arg tuple args: positional arguments to initialize the frame
        :arg dict options: ``config`` options to initialize the frame

        All widgets for this frame are initialized, but layout is
        deferred until methods are called to transition between states.

        """
        self.__log.call(*args, **options)
        super().__init__(*args, **options)

        # all editor widgets and vars are referenced through this mapping;
        # these are never destroyed, only updated
        self.__metadata_editors = dict()

        self.__init_album_editors()
        self.__init_track_editors()

    def __init_album_editors(self):
        """Create the entry/selection widgets for album metadata fields.

        """
        album_editor_frame = LabelFrame(
            self, name="album_editor_frame", text="Album")

        create_album_choices_editor = partial(
            self._create_choices_editor, album_editor_frame, "album")

        self.__row = 0  # relative to album_editor_frame

        create_album_choices_editor("title", tracks_apply=False)
        self._create_album_disc_editor(album_editor_frame)
        self._create_album_compilation_editor(album_editor_frame)
        create_album_choices_editor("artist")
        create_album_choices_editor("label", tracks_apply=False)
        create_album_choices_editor("genre")
        create_album_choices_editor("year")
        self._create_album_cover_editor(album_editor_frame)
        self._create_album_custom_metadata_editor(album_editor_frame)

        album_editor_frame.grid_columnconfigure(0, weight=0)
        album_editor_frame.grid_columnconfigure(1, weight=1)
        album_editor_frame.grid_columnconfigure(2, weight=0)

        album_editor_frame.pack(anchor=N, fill=BOTH)

    def __init_track_editors(self):
        """Create the entry/selection widgets for track metadata fields.

        """
        track_editor_frame = LabelFrame(
            self, name="track_editor_frame", text="Tracks")

        create_track_choices_editor = partial(
            self._create_choices_editor, track_editor_frame, "track",
            tracks_apply=False)

        self.__row = 0  # relative to track_editor_frame

        self._create_track_control_editors(track_editor_frame)
        create_track_choices_editor("title")
        create_track_choices_editor("artist")
        create_track_choices_editor("genre")
        create_track_choices_editor("year")
        self._create_track_custom_metadata_editor(track_editor_frame)

        track_editor_frame.grid_columnconfigure(0, weight=0)
        track_editor_frame.grid_columnconfigure(1, weight=1)

        track_editor_frame.pack(anchor=N, pady=11, fill=BOTH)

    def _create_choices_editor(
            self, parent, editor_name, field_name, tracks_apply=True,
            VarType=StringVar):
        """Create an entry/selection widget for a metadata field that
        may have multiple aggregated values.

        :arg parent: parent object of the editor
        :arg str editor_name: either "album" or "track"
        :arg str field_name: the metadata field name
        :keyword bool tracks_apply:
           whether or not to create an "Apply to all tracks" button that
           corresponds to *field_name*
        :keyword VarType:
           the :mod:`tkinter` variable type that will hold the value of
           the metadata field
        :return: the metadata field entry/selection widget
        :rtype: :class:`tkinter.ttk.Combobox`

        """
        self.__log.call(
            parent, editor_name, field_name, tracks_apply=tracks_apply,
            VarType=VarType)

        Label(parent, text=field_name.capitalize()).grid(
            row=self.__row, column=0, padx=_PADX, pady=_PADY, sticky=E)

        name = "%s_%s" % (editor_name, field_name)

        # NOTE: for track editors, the variable will be re-*configured* on a
        # per-track basis as the tracks spinbox is manipulated; however, the
        # var *property* of a track editor is never re-assigned
        var = VarType(name="%s_var" % name)
        combobox = Combobox(
            parent, name="%s_combobox" % name, textvariable=var
        )
        # attach the variable to the Combobox for easy access
        combobox.var = var
        if field_name != "year":
            sticky = W + E
        else:
            combobox.configure(width=7)
            sticky = W
        combobox.grid(
            row=self.__row, column=1, padx=_PADX, pady=_PADY, sticky=sticky)

        self.__metadata_editors[name] = combobox

        if tracks_apply:
            Button(
                parent, name="%s_apply_button" % name,
                text="Apply %s to all tracks" % field_name,
                command=lambda
                        self=self, metadata_name="track_%s" % field_name,
                        var=var:
                    self.__apply_to_all_tracks(metadata_name, var.get())
            ).grid(
                row=self.__row, column=2, padx=_PADX, pady=_PADY, sticky=W+E)

        self.__row += 1

        return combobox

    def __apply_to_all_tracks(self, field_name, value):
        """Set **all** track variables for *field_name* to *value*.

        :arg str field_name: the metadata field name
        :arg value:
           the variable value to set (type varies by metadata field)

        """
        self.__log.call(field_name, value)

        for track_vars in self.__track_vars[1:]:
            track_vars[field_name].set(value)

    def _create_album_disc_editor(self, parent):
        """Create entry widgets for the album disc number/total fields.

        :arg parent: parent object of the entry widgets

        """
        self.__log.call(parent)

        Label(parent, text="Disc").grid(
            row=self.__row, column=0, padx=_PADX, pady=_PADY, sticky=E)

        frame = Frame(parent)

        number_var = IntVar(name="album_discnumber_var")
        album_discnumber = Entry(
            frame, name="album_discnumber_entry", exportselection=NO, width=3,
            textvariable=number_var)
        album_discnumber.var = number_var
        album_discnumber.pack(side=LEFT)

        self.__metadata_editors["album_discnumber"] = album_discnumber

        Label(frame, text="of").pack(side=LEFT, padx=_PADX)

        total_var = IntVar(name="album_disctotal_var")
        album_disctotal = Entry(
            frame, name="album_disctotal_entry", exportselection=NO, width=3,
            textvariable=total_var)
        album_disctotal.var = total_var
        album_disctotal.pack(side=LEFT)

        self.__metadata_editors["album_disctotal"] = album_disctotal

        frame.grid(
            row=self.__row, column=1, columnspan=2, padx=_PADX, pady=_PADY,
            sticky=W)

        self.__row += 1

    def _create_album_compilation_editor(self, parent):
        """Create the toggle widget for the album compilation field.

        :arg parent: parent object of the toggle widget

        """
        self.__log.call(parent)

        Label(parent, text="Compilation").grid(
            row=self.__row, column=0, padx=_PADX, pady=_PADY, sticky=E)

        var = BooleanVar()
        album_compilation = Checkbutton(
            parent, name="album_compilation_checkbutton", variable=var,
            onvalue=True, offvalue=False)
        album_compilation.var = var
        album_compilation.grid(
            row=self.__row, column=1, columnspan=2, padx=_PADX, pady=_PADY,
            sticky=W)

        self.__metadata_editors["album_compilation"] = album_compilation

        self.__row += 1

    def _create_album_cover_editor(self, parent):
        """Create selection widget and import buttons for the album
        cover field.

        :arg parent:
           parent object of the frame that will contain the selection
           widget and buttons

        """
        self.__log.call(parent)

        Label(parent, text="Cover").grid(
            row=self.__row, column=0, padx=_PADX, pady=_PADY, sticky=E)

        frame = Frame(parent)

        var = StringVar(name="album_cover_var")
        album_cover = OptionMenu(frame, var)
        album_cover.var = var
        album_cover.config(state=DISABLED)
        album_cover.pack(side=LEFT)

        self.__metadata_editors["album_cover"] = album_cover

        Button(
            frame, name="album_cover_add_url_button", text="Add URL",
            command=self._add_album_cover_from_url
        ).pack(side=LEFT, padx=_PADX)

        Button(
            frame, name="album_cover_add_file_button", text="Add file",
            command=self._add_album_cover_from_file
        ).pack(side=LEFT, padx=_PADX)

        frame.grid(
            row=self.__row, column=1, columnspan=2, padx=_PADX, pady=_PADY,
            sticky=W)

        self.__row += 1

    def choose_album_cover(self, label):
        """Select and preview a cover image.

        :arg str label: the cover image display label

        .. note::
           The Mac OS X "Preview" app is used to open the album cover
           image identified by *label*.

        """
        self.__log.call(label)

        filename = self.__album_covers.get(label)
        if filename is None:
            self.__log.error("%r not found in album covers", label)
            messagebox.showerror(
                "Album cover preview failure",
                "Album cover %r was not found!" % label)
            return

        status = subprocess.call(["open", "-a", "Preview", filename])
        if status == 0:
            self.__metadata_editors["album_cover"].var.set(label)
        else:
            self.__log.warning(
                "exit status %d attempting to preview %s (%s)",
                status, label, filename)

    def _add_album_cover_from_url(self):
        """Download a cover image from a user-provided URL and open in
        Mac OS X Preview.

        The downloaded cover image is made available in the cover image
        dropdown.

        """
        self.__log.call()

        album_cover = self.__metadata_editors["album_cover"]

        album_cover.config(state=DISABLED)

        # leave initialvalue empty (paste-over doesn't work in XQuartz)
        url = simpledialog.askstring(
            "Add a cover image from a URL", "Enter the image URL:",
            initialvalue="")
        if not url:
            album_cover.config(state=NORMAL)
            self.__log.return_()
            return

        self.__log.debug("url = %r", url)
        label = None
        try:
            response = urlopen(
                url, timeout=get_config().getfloat("HTTP", "timeout"))
            image_data = response.read()
            response.close()

            if response.status != 200:
                raise RuntimeError(
                    "HTTP %d %s" % (response.status, response.reason))

            image_type = imghdr.what("_ignored_", h=image_data)
            if image_type is None:
                raise MetadataError(
                    "Unrecognized image type.",
                    context_hint="Add cover image from URL")

            filename = make_tempfile(suffix='.' + image_type)
            with open(filename, "wb") as f:
                f.write(image_data)
            self.__log.debug("wrote %s", filename)
        except Exception as e:
            self.__log.exception("failed to obtain image from %r", url)
            messagebox.showerror(
                "Image download failure",
                "An unexpected error occurred while "
                    "downloading the image from %s." % url)
        else:
            label = self.__add_album_cover_option(filename)
        finally:
            album_cover.config(state=NORMAL)

        if label is not None:
            self.choose_album_cover(label)

    def _add_album_cover_from_file(self):
        """Open a user-defined cover image in Mac OS X Preview.

        The cover image is made available in the cover image dropdown.

        """
        self.__log.call()

        album_cover = self.__metadata_editors["album_cover"]

        album_cover.config(state=DISABLED)

        label = None
        filename = filedialog.askopenfilename(
            defaultextension=".jpg",
            filetypes=[
                ("JPEG", "*.jpg"),
                ("JPEG", "*.jpeg"),
                ("PNG", "*.png"),
            ],
            initialdir=os.path.expanduser("~/Pictures"),
            title="Choose a JPEG or PNG file")

        if not filename:
            album_cover.config(state=NORMAL)
            self.__log.return_()
            return
        elif not os.path.isfile(filename):
            self.__log.error("file not found: %s", filename)
            messagebox.showerror(
                "File not found", "File not found: %s" % filename)
            self.__log.return_()
            return

        self.__log.debug("filename = %r", filename)
        try:
            with open(filename, "rb") as f:
                image_data = f.read()

            image_type = imghdr.what("_ignored_", h=image_data)
            if image_type is None:
                raise MetadataError(
                    "Unrecognized image type.",
                    context_hint="Add cover image from file")
        except Exception as e:
            self.__log.exception("failed to identify image from %r", filename)
            messagebox.showerror(
                "Image add failure",
                "An unexpected error occurred while "
                    "processing the image from %s." % filename)
        else:
            label = self.__add_album_cover_option(filename)
        finally:
            album_cover.config(state=NORMAL)

        if label is not None:
            self.choose_album_cover(label)

    def __add_album_cover_option(self, filename, showinfo=True):
        """Add an entry for *filename* to the album cover dropdown.

        :arg str filename: absolute path to an album cover image file
        :keyword bool showinfo:
           whether or not to open a dialog confirming the addition of
           *filename*

        """
        self.__log.call(filename, showinfo=showinfo)

        label = "%02d (%s)" % (
            len(self.__album_covers) + 1, os.path.basename(filename))
        self.__album_covers[label] = filename

        self.__metadata_editors["album_cover"]["menu"].add_command(
            label=label,
            command=
                lambda self=self, label=label:
                    self.choose_album_cover(label))

        if showinfo:
            messagebox.showinfo(
                "Cover image added",
                "%r has been added to the list of available covers." % label)

        self.__log.return_(label)
        return label

    def _create_album_custom_metadata_editor(self, parent):
        """Create the UI editing controls for editing custom metadata
        for *all* tracks.

        :arg parent: parent object of the editor

        """
        self.__log.call(parent)

        album_custom_tagging_button = Button(
            parent, name="album_custom_tagging_button",
            text="Edit custom Vorbis/ID3v2 tagging for ALL tracks",
            command=
                lambda self=self:
                    EditAlbumCustomMetadataTaggingDialog(
                        parent, self.__aggregated_metadata,
                        self.__aggregated_metadata["__tracks"],
                        title=self.__metadata_editors["album_title"].var.get())
        )
        album_custom_tagging_button.grid(
            row=self.__row, column=0, columnspan=3, padx=_PADX, pady=_PADY,
            sticky=W+E)

        self.__metadata_editors["album_custom"] = album_custom_tagging_button

        self.__row += 1

    def _create_track_control_editors(self, parent):
        """Create the widgets used to navigate and include/exclude
        individual tracks.

        :arg parent:
           parent object of the frames that contain the control widgets

        """
        self.__log.call(parent)

        Label(parent, text="Track").grid(
            row=self.__row, column=0, padx=_PADX, pady=_PADY, sticky=E)

        nav_frame = Frame(parent, name="track_nav_frame")

        number_var = IntVar(name="track_number_var")
        track_number = Spinbox(
            nav_frame, name="track_number_spinbox",
            from_=0, to=0, textvariable=number_var, width=3, wrap=True,
            state="readonly", command=self._refresh_track_editors
        )
        track_number.var = number_var
        track_number.pack(side=LEFT, padx=_PADX)

        of_label = Label(nav_frame, name="of_label", text="of")
        of_label.pack(side=LEFT)
        track_number.of_label = of_label

        self.__metadata_editors["track_number"] = track_number

        nav_frame.grid(row=self.__row, column=1, sticky=W)

        self.__row += 1

        include_label = Label(parent, text="Include").grid(
            row=self.__row, column=0, padx=_PADX, pady=_PADY, sticky=E)

        include_var = BooleanVar(name="track_include_var", value=True)
        track_include = Checkbutton(
            parent, name="track_include_checkbutton",
            variable=include_var, onvalue=True, offvalue=False,
            command=self.__update_track_include_state)
        track_include.var = include_var
        track_include.grid(row=self.__row, column=1, padx=_PADX, sticky=W)

        apply_include_button = _styled(
            Button(
                parent, name="track_include_apply_button",
                text="Include all tracks",
                command=lambda self=self:
                    self.__apply_to_all_tracks(
                        "track_include",
                        self.__metadata_editors["track_include"].var.get())),
            foreground="Blue")
        apply_include_button.grid(
            row=self.__row, column=2, padx=_PADX, sticky=W)
        track_include.apply_button = apply_include_button

        self.__metadata_editors["track_include"] = track_include

        self.__row += 1

    def _create_track_custom_metadata_editor(self, parent):
        """Create the UI editing controls for editing custom metadata
        for a single track.

        :arg parent: parent object of the editor

        """
        self.__log.call(parent)

        track_custom_tagging_button = Button(
            parent, name="track_custom_tagging_button",
            text="Edit custom Vorbis/ID3v2 tagging for this track",
            command=
                lambda self=self:
                    EditCustomMetadataTaggingDialog(
                        parent,
                        self.__aggregated_metadata["__tracks"][
                            self.current_track_number],
                        title="%d. %s" % (
                            self.current_track_number,
                            self.__track_vars[self.current_track_number][
                                "track_title"].get()))
        )
        track_custom_tagging_button.grid(
            row=self.__row, column=0, columnspan=3, padx=_PADX, pady=_PADY,
            sticky=W+E)

        self.__metadata_editors["track_custom"] = track_custom_tagging_button

        self.__row += 1

    @property
    def current_track_number(self):
        """The number of the track currently being edited."""
        return self.__metadata_editors["track_number"].var.get()

    def metadata_ready_for_editing(self, aggregated_metadata):
        """(Re)Configure the entry/selection widgets with values from
        *aggregated_metadata*.

        :arg dict aggregated_metadata:
           the aggregated metadata field values for the current album
           and each of its tracks

        Separate variables will be created to store the entered/selected
        values for each track.

        """
        self.__log.call(aggregated_metadata)

        self.reset()

        metadata_editors = self.__metadata_editors

        for album_field_name in [
                "album_title",
                "album_artist",
                "album_label",
                "album_genre",
                "album_year",
                ]:
            widget = metadata_editors[album_field_name]
            widget.configure(values=aggregated_metadata[album_field_name])
            if aggregated_metadata[album_field_name]:
                widget.current(0)

        metadata_editors["album_discnumber"].var.set(
            aggregated_metadata["album_discnumber"])
        metadata_editors["album_disctotal"].var.set(
            aggregated_metadata["album_disctotal"])

        metadata_editors["album_compilation"].var.set(
            aggregated_metadata["album_compilation"])

        album_cover_editor = metadata_editors["album_cover"]
        album_cover_editor.config(state=DISABLED)
        if aggregated_metadata["album_cover"]:
            for filepath in aggregated_metadata["album_cover"]:
                self.__add_album_cover_option(filepath, showinfo=False)
            album_cover_editor.config(state=NORMAL)

        self.__aggregated_metadata = deepcopy(aggregated_metadata)

        self._initialize_track_vars()

        # if persisted data was restored, manually select the cover image so
        # that it opens in Preview automatically
        fm = self.master
        if fm._persistence.restored and self.__album_covers:
            self.choose_album_cover(list(self.__album_covers.keys())[0])

    def pack(self, *args, **kwargs):
        """Display the editor frame.

        :arg tuple args: positional arguments to pack the editor frame
        :arg dict kwargs: keyword argument to pack the editor frame

        Showing the editor frame *enables* the **File | Save metadata**
        menu command.

        """
        self.__log.call(*args, **kwargs)
        super().pack(*args, **kwargs)

        fm = self.master

        # Enable the "Save metadata" command in the File menu
        file_menu = fm.nametowidget(".menubar.file_menu")
        file_menu.entryconfig(0, state=NORMAL)

    def pack_forget(self):
        """Hide the editor frame.

        Hiding the editor frame *disables* the **File | Save metadata**
        menu command.

        """
        self.__log.call()
        super().pack_forget()

        fm = self.master

        # Disable the "Save metadata" command in the File menu
        file_menu = fm.nametowidget(".menubar.file_menu")
        file_menu.entryconfig(0, state=DISABLED)

    @property
    def metadata_snapshot(self):
        """The complete (album and tracks) metadata mapping as currently
        represented by all editor widgets and variables.

        :rtype: :class:`collections.OrderedDict`

        """
        self.__log.call()

        metadata_editors = self.__metadata_editors

        snapshot = OrderedDict()
        for album_field_name in [
                "album_title",
                "album_discnumber",
                "album_disctotal",
                "album_compilation",
                "album_artist",
                "album_label",
                "album_genre",
                "album_year",
                ]:
            snapshot[album_field_name] = \
                metadata_editors[album_field_name].var.get()

        # use the actual temp filename for the snapshot
        snapshot["album_cover"] = \
            self.__album_covers.get(metadata_editors["album_cover"].var.get())

        track_vars = self.__track_vars
        snapshot["album_tracktotal"] = len(track_vars) - 1

        # for the metadata snapshot, the custom fields must be preserved at
        # both the album and track level so that a disc whose persisted
        # metadata is re-read will still have its custom metadata editable as
        # expected
        aggregated_metadata = self.__aggregated_metadata
        snapshot["__custom"] = aggregated_metadata["__custom"].copy()

        snapshot["__tracks"] = [None]
        for t in range(1, len(track_vars)):
            track_metadata = OrderedDict()

            track_metadata["track_number"] = t
            for track_field_name in [
                    "track_include",
                    "track_title",
                    "track_artist",
                    "track_genre",
                    "track_year",
                    ]:
                track_metadata[track_field_name] = \
                    track_vars[t][track_field_name].get()

            track_metadata["__custom"] = \
                aggregated_metadata["__tracks"][t]["__custom"].copy()

            snapshot["__tracks"].append(track_metadata)

        self.__log.return_(snapshot)
        return snapshot

    @property
    def flattened_metadata(self):
        """The complete per-track metadata, including album metadata
        values shared by all tracks.

        :rtype: :obj:`list` of :class:`collections.OrderedDict`

        .. note::
           The list returned by this property uses zero-based indexing.
           (This differs from most other internal representations of
           per-track metadata, which use 1-based indexing to maintain
           consistency with respect to track numbers.)

        """
        self.__log.call()

        snapshot = self.metadata_snapshot
        snapshot.pop("__custom") # already incorporated into each track

        # to "flatten" the metadata, just add the album metadata to each track
        flattened = snapshot.pop("__tracks")[1:]    # zero-based indexing here
        for i in range(len(flattened)):
            flattened[i].update(snapshot)

        self.__log.return_(flattened)
        return flattened

    def _initialize_track_vars(self):
        """Create (and set the initial values of) variables for metadata
        fields for each track.

        """
        self.__log.call()

        track_vars = self.__track_vars = [
            None,   # track vars use 1-based indexing
        ]

        aggregated_tracks_metadata = self.__aggregated_metadata["__tracks"]
        last_track = len(aggregated_tracks_metadata) - 1
        # from_ will still be 0 here, and that's intended - it means that when
        # we invoke "buttonup" for the first time, it will increment the track
        # spinbox to 1, triggering a refresh of track 1's metadata
        track_number_editor = self.__metadata_editors["track_number"]
        track_number_editor.config(to=last_track)
        track_number_editor.of_label.config(text="of %d" % last_track)

        # tracks metadata also uses 1-based indexing
        for t in range(1, len(aggregated_tracks_metadata)):
            track_metadata = aggregated_tracks_metadata[t]

            # first initialize the individual track vars...
            varmap = {
                "track_include": BooleanVar(
                    name="track_%d_include" % t,
                    value=track_metadata["track_include"]),
            }
            for field in [
                    "title",
                    "artist",
                    "genre",
                    "year",
                    ]:
                metadata_name = "track_%s" % field
                varmap[metadata_name] = StringVar(
                    name="track_%d_%s" % (t, field),
                    value=track_metadata[metadata_name][0]
                        if track_metadata[metadata_name] else "")

            track_vars.append(varmap)

            # ...then initialize the editors and editor vars by using the track
            # spinbox to trigger refreshes (but make sure this method is called
            # BEFORE the metadata editor is packed, otherwise the user will be
            # very disoriented and confused)
            track_number_editor.invoke("buttonup")

        # now update the from_ to 1 and initialize the spinner to track #1 by
        # "wrapping around"
        track_number_editor.config(from_=1)
        track_number_editor.invoke("buttonup")

    def _refresh_track_editors(self):
        """Populate track editors with metadata for the current track.

        This method is called when navigating to another track. If
        navigation is at either end of the track list already (i.e.
        navigating down from track 1 or up from the last track), then
        the spinner "wraps around."

        """
        self.__log.call()

        track_number = self.current_track_number
        track_vars = self.__track_vars[track_number]
        track_metadata = self.__aggregated_metadata["__tracks"][track_number]

        metadata_editors = self.__metadata_editors
        metadata_editors["track_include"].var.set(
            track_vars["track_include"].get())
        self.__update_track_include_state()
        for track_field_name in [
                "track_title",
                "track_artist",
                "track_genre",
                "track_year",
                ]:
            widget = metadata_editors[track_field_name]
            widget.configure(
                values=track_metadata[track_field_name],
                textvariable=track_vars[track_field_name])

    def __update_track_include_state(self):
        """Update the widgets for the current track based on whether or
        not it is included.

        """
        self.__log.call()

        track_number = self.current_track_number
        track_include_editor = self.__metadata_editors["track_include"]

        track_included = track_include_editor.var.get()
        self.__track_vars[track_number]["track_include"].set(track_included)

        if track_included:
            track_include_editor.apply_button.config(text="Include all tracks")
            _styled(track_include_editor.apply_button, foreground="Blue")
        else:
            track_include_editor.apply_button.config(text="Exclude all tracks")
            _styled(track_include_editor.apply_button, foreground="Red")

        if get_config()["UI"].getboolean("disable_editing_excluded_tracks"):
            for track_field_name in [
                    "track_title",
                    "track_artist",
                    "track_genre",
                    "track_year",
                    "track_custom",
                    ]:
                self.__metadata_editors[track_field_name].config(
                    state=NORMAL if track_included else DISABLED)

    def reset(self):
        """Populate widgets in their default/initial states."""
        self.__log.call()

        self._remove()

        metadata_editors = self.__metadata_editors

        # reset all track editor vars to default
        # (the .var property of track editors serves as the default)
        metadata_editors["track_include"].var.set(True)
        for track_field_name in [
                "track_title",
                "track_artist",
                "track_genre",
                "track_year",
                ]:
            metadata_editors[track_field_name].configure(
                textvariable=metadata_editors[track_field_name].var)

        metadata_editors["album_discnumber"].var.set(0)
        metadata_editors["album_disctotal"].var.set(0)

        metadata_editors["album_compilation"].var.set(False)

        for field_name in [
                "album_title",
                "album_artist",
                "album_label",
                "album_genre",
                "album_year",
                "track_title",
                "track_artist",
                "track_genre",
                "track_year",
                ]:
            widget = metadata_editors[field_name]
            widget.configure(values=[])
            widget.var.set("")

        album_cover_editor = metadata_editors["album_cover"]
        album_cover_editor["menu"].delete(0, END)
        album_cover_editor.var.set("")
        album_cover_editor.config(state=DISABLED)

        track_number_editor = metadata_editors["track_number"]
        track_number_editor.config(from_=0, to=0)
        track_number_editor.var.set(0)

        track_include_editor = metadata_editors["track_include"]
        # tracks are always included by default
        track_include_editor.var.set(True)
        track_include_editor.apply_button.config(text="Include all tracks")
        _styled(track_include_editor.apply_button, foreground="Blue")

        # finally clear all cached data
        self.__aggregated_metadata = None
        self.__album_covers = OrderedDict()
        self.__track_vars = None

    def _remove(self):
        """Remove widgets from the current layout."""
        self.__log.call()

        # entry widgets are NOT removed - just the editor frame itself
        self.pack_forget()


@logged
class _FMEncodingStatusFrame(LabelFrame):
    """The encoding status (monitoring) frame for FLACManager."""

    def __init__(self, *args, **options):
        """
        :arg tuple args: positional arguments to initialize the frame
        :arg dict options: ``config`` options to initialize the frame

        """
        self.__log.call(*args, **options)
        super().__init__(*args, **options)

        list_frame = Frame(self)

        vscrollbar = Scrollbar(list_frame, orient=VERTICAL)
        vscrollbar.pack(side=RIGHT, fill=Y, padx=0, pady=0)

        self._track_encoding_status_list = Listbox(
            list_frame, name="track_encoding_status_listbox",
            exportselection=NO, activestyle=NONE, selectmode=SINGLE,
            borderwidth=0, yscrollcommand=vscrollbar.set)
        self._track_encoding_status_list.pack(fill=BOTH, padx=0, pady=0)

        vscrollbar.config(command=self._track_encoding_status_list.yview)

        list_frame.pack(fill=BOTH, padx=_PADX, pady=_PADY)

    def ready_to_encode(self, per_track_metadata):
        """(Re)Initialize the encoding status list to monitor encoding
        of tracks from *per_track_metadata*.

        :arg list per_track_metadata:
           metadata field mappings for each track

        """
        track_encoding_statuses = [
            TrackEncodingStatus(
                "{track_number:02d} {track_title}".format(**track_metadata),
                pending=track_metadata["track_include"])
            for track_metadata in per_track_metadata]

        # determine how many tracks are visible at once
        number_of_tracks = len(per_track_metadata)
        max_visible_tracks = get_config()["UI"].getint(
            "encoding_max_visible_tracks")
        visible_tracks = (
            number_of_tracks if number_of_tracks < max_visible_tracks
            else max_visible_tracks)

        items_spec = StringVar(
            value=' '.join(
                "{%s}" % track_encoding_status.describe()
                for track_encoding_status in track_encoding_statuses))
        self._track_encoding_status_list.configure(
            height=visible_tracks, listvariable=items_spec)

        for i in range(number_of_tracks):
            if not per_track_metadata[i]["track_include"]:
                self._track_encoding_status_list.itemconfig(i, {"fg": "gray79"})

        self._track_encoding_statuses = track_encoding_statuses

    def encoding_in_progress(self):
        """Update the UI as tracks are ripped."""
        # don't log entry into this method - it is called repeatedly until all
        # tracks are ripped
        try:
            (priority, status) = _ENCODING_QUEUE.get_nowait()
        except queue.Empty:
            self.after(QUEUE_GET_NOWAIT_AFTER, self.encoding_in_progress)
        else:
            _ENCODING_QUEUE.task_done()
            self.__log.debug("dequeued %r", status)

            (track_index, cdda_fn, flac_fn, stdout_fn, target_state) = status
            if target_state == "FINISHED":
                # all tracks have been processed
                while _ENCODING_QUEUE.qsize() > 0:
                    try:
                        self.__log.debug(
                            "finished; discarding %r",
                            _ENCODING_QUEUE.get_nowait())
                    except queue.Empty:
                        break
                    else:
                        _ENCODING_QUEUE.task_done()

                fm = self.master
                fm._disc_frame.rip_and_tag_finished()
                fm.bell()

                self.__log.trace("exit the monitoring loop")
                return False

            track_encoding_status = self._track_encoding_statuses[track_index]

            # only process "expected" state transitions
            if track_encoding_status.transition_to(target_state):
                if track_encoding_status.state == TRACK_FAILED:
                    status_message = track_encoding_status.describe(
                        message="%s: %s" %
                            (target_state.__class__.__name__, target_state)
                        if isinstance(target_state, Exception) else None)
                    item_config = {"fg": "red"}
                elif track_encoding_status.state == TRACK_ENCODING_FLAC:
                    # ensure that the currently-ripping track is always visible
                    self._track_encoding_status_list.see(track_index)
                    # read encoding interval status from flac's stdout
                    cdda_basename = os.path.basename(cdda_fn)
                    stdout_message = self._read_current_status(
                        cdda_basename, stdout_fn)
                    status_message = track_encoding_status.describe(
                        message=stdout_message if stdout_message else None)
                    item_config = {"fg": "blue"}
                elif (track_encoding_status.state in [
                            TRACK_DECODING_WAV,
                            TRACK_ENCODING_MP3]
                        or track_encoding_status.state.key ==
                            "REENCODING_MP3"):
                    status_message = track_encoding_status.describe()
                    item_config = {"fg": "dark violet"}
                elif track_encoding_status.state == TRACK_COMPLETE:
                    status_message = flac_fn
                    item_config = {"fg": "dark green"}
                else:   # unexpected state
                    status_message = "%s (unexpected target state %s)" % (
                        track_encoding_status.describe(), target_state)
                    item_config = {"fg": "red"}

                self._track_encoding_status_list.delete(track_index)
                self._track_encoding_status_list.insert(
                    track_index, status_message)
                self._track_encoding_status_list.itemconfig(
                    track_index, item_config)

                # ensure that last track is always visible after delete/insert
                if (track_index ==
                        self._track_encoding_status_list.index(END) - 1):
                    self._track_encoding_status_list.see(track_index)

            self.after(QUEUE_GET_NOWAIT_AFTER, self.encoding_in_progress)

            return True

    def _read_current_status(self, cdda_basename, stdout_fn):
        """Extract the most recent FLAC encoding update from
        *stdout_fn*.

        :arg str cdda_basename: a grep pattern for *stdout_fn*
        :arg str stdout_fn:
           filename to which stdout has been redirected
        :return: a line of update text from *stdout_fn*
        :rtype: :obj:`str`

        """
        # do not trace; called from a recursive method
        status_line = None
        prefix = "%s: " % cdda_basename
        with open(stdout_fn, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith(prefix):
                    # remove the prefix, then split on ASCII BS (Backspace) and
                    # take the last component
                    #
                    # output line looks like this:
                    #   ${prefix}${status1}(BS)+${status2}(BS)+..${statusN}
                    status_line = line.replace(prefix, "").split('\x08')[-1]
        return status_line

    def reset(self):
        """Populate widgets in their default/initial states."""
        self.__log.call()

        self._remove()

        self._track_encoding_status_list.delete(0, END)
        self._track_encoding_status_list.configure(listvariable=None, height=0)

        self._track_encoding_statuses = None

    def _remove(self):
        """Remove widgets from the current layout."""
        self.__log.call()

        # widgets are NOT removed - just the frame itself
        self.pack_forget()


@total_ordering
class TrackState:
    """Represents the state of a single track at any given time during
    the rip-and-tag operation.

    """

    def __init__(self, ordinal, key, text):
        """
        :arg int ordinal: this state's relative value
        :arg str key: uniquely identifies this state
        :arg str text: a short description of this state

        """
        self._ordinal = ordinal
        self._key = key
        self._text = text

    @property
    def key(self):
        """The unique identifier for this state."""
        return self._key

    @property
    def text(self):
        """The track-independent short description of this state."""
        return self._text

    def __int__(self):
        """Return the relative (ordinal) value of this state."""
        return self._ordinal

    def __str__(self):
        """Return the unique identifier for this state."""
        return self._key

    def __repr__(self):
        """Return a string that is unique for this track state."""
        return "%s(%d, %r, %r)" % (
            self.__class__.__name__, self._ordinal, self._key, self._text)

    def __lt__(self, other):
        """Return ``True`` if this state's ordinal value is less than
        *other* state's ordinal value, otherwise ``False``.

        :arg flacmanager.TrackState other: the state being compared

        """
        return int(self) < int(other)

    def __eq__(self, other):
        """Return ``True`` if this state's ordinal value and key are
        equal to *other* state's ordinal value and key, otherwise
        ``False``.

        :arg flacmanager.TrackState other: the state being compared

        """
        return (
            isinstance(other, self.__class__)
            and self._ordinal == other._ordinal
            and self._key == other._key)

    def __hash__(self):
        return hash(repr(self))


#: Indicates that a track is excluded from the rip-and-tag operation.
TRACK_EXCLUDED = TrackState(-1, "EXCLUDED", "excluded")

#: Indicates that the rip-and-tag process has not yet begun for a track.
TRACK_PENDING = TrackState(0, "PENDING", "pending\u2026")

#: Indicates that a track is being encoded from CDDA to FLAC format.
TRACK_ENCODING_FLAC = TrackState(
    1, "ENCODING_FLAC", "encoding CDDA to FLAC\u2026")

#: Indicates that a track is being decoded from FLAC to WAV format.
TRACK_DECODING_WAV = TrackState(
    2, "DECODING_WAV", "decoding FLAC to WAV\u2026")

#: Indicates that a track is being encoded from WAV to MP3 format.
TRACK_ENCODING_MP3 = TrackState(3, "ENCODING_MP3", "encoding WAV to MP3\u2026")

#: Indicates that a track is being re-encoded from WAV to MP3 format
#: after clipping was detected in a prior encoding operation.
TRACK_REENCODING_MP3 = partial(
    lambda scale: TrackState(
        4, "REENCODING_MP3",
        "re-encoding MP3 at {:.2f} scale (clipping detected)\u2026".
            format(scale)))

#: Indicates that an error occurred while processing a track.
TRACK_FAILED = TrackState(99, "FAILED", "failed")

#: Indicates that the rip-and-tag process has finished for a track.
TRACK_COMPLETE = TrackState(99, "COMPLETE", "complete")


@logged
class TrackEncodingStatus:
    """A simple state machine for a single track's encoding status."""

    def __init__(self, track_label, pending=True):
        """
        :arg str track_label: the track's display label
        :keyword bool pending:
           the default ``True`` initializes status as
           :data:`TRACK_PENDING`; set to ``False`` to initialize status
           as :data:`TRACK_EXCLUDED`

        """
        self.__log.call(track_label, pending=pending)

        self.track_label = track_label
        self.__state = TRACK_PENDING if pending else TRACK_EXCLUDED

    @property
    def state(self):
        """The current state of encoding for this track."""
        return self.__state

    def transition_to(self, to_state):
        """Advance this track's encoding state from its current state to
        *to_state*, if permitted.

        :arg to_state:
           the target encoding state, or any :class:`Exception` to
           transition to :data:`TRACK_FAILED`
        :return:
           ``True`` if the transition is successful, otherwise ``False``

        """
        self.__log.call(to_state)

        from_state = self.__state
        if isinstance(to_state, Exception):
            to_state = TRACK_FAILED

        if (from_state in [
                    TRACK_EXCLUDED,
                    TRACK_FAILED,
                    TRACK_COMPLETE]
                or to_state < from_state):
            self.__log.warning(
                "%s: illegal transition from %s to %s",
                self.track_label, from_state, to_state)
            self.__log.return_(False)
            return False

        self.__state = to_state
        self.__log.return_(True)
        return True

    def describe(self, message=None):
        """Return a short display string for this track and its current
        status.

        :arg str message:
           short piece of text to use with the track label (instead of
           the default message for the current state)

        """
        return "%s: %s" % (
            self.track_label,
            message if message is not None else self.__state.text)


def generate_flac_dirname(library_root, metadata):
    """Build the directory for a track's FLAC file.

    :arg str library_root: the FLAC library directory
    :arg dict metadata: the finalized metadata for a single track
    :return: an absolute directory path
    :rtype: :obj:`str`

    """
    _log.call(library_root, metadata)
    return _generate_dirname("FLAC", library_root, metadata)


def generate_flac_basename(metadata):
    """Build the filename for a track's FLAC file.

    :arg dict metadata: the finalized metadata for a single track
    :return: a relative file name
    :rtype: :obj:`str`

    """
    _log.call(metadata)
    return _generate_basename("FLAC", metadata)


def generate_mp3_dirname(library_root, metadata):
    """Build the directory for a track's MP3 file.

    :arg str library_root: the MP3 library directory
    :arg dict metadata: the finalized metadata for a single track
    :return: an absolute directory path
    :rtype: :obj:`str`

    """
    _log.call(library_root, metadata)
    return _generate_dirname("MP3", library_root, metadata)


def generate_mp3_basename(metadata):
    """Build the filename for a track's MP3 file.

    :arg dict metadata: the finalized metadata for a single track
    :return: a relative file name
    :rtype: :obj:`str`

    """
    _log.call(metadata)
    return _generate_basename("MP3", metadata)


def _generate_dirname(section, library_root, metadata):
    """Build the directory for a track's FLAC or MP3 file.

    :arg str section: "FLAC" or "MP3"
    :arg str library_root: the MP3 library directory
    :arg dict metadata: the finalized metadata for a single track
    :return: an absolute directory path
    :rtype: :obj:`str`

    """
    _log.call(section, library_root, metadata)

    config = get_config()

    ndisc = "ndisc_" if metadata["album_disctotal"] > 1 else ""
    is_compilation = metadata["album_compilation"]
    folder_format_spec = (
        config[section][ndisc + "album_folder"] if not is_compilation
        else config[section][ndisc + "compilation_album_folder"])
    _log.debug("using template %r", folder_format_spec)

    folder_names = [
        name_format_spec.format(**metadata)
        for name_format_spec in folder_format_spec.split('/')]
    _log.debug("raw folder names %r", folder_names)

    if config[section].getboolean("use_xplatform_safe_names"):
        # paranoid-safe and compact, but less readable
        folder_names = _xplatform_safe(folder_names)
    else:
        # as close to format spec as possible, but still relatively safe
        folder_names = [
            re.sub(r"[^0-9a-zA-Z-.,_() ]", '_', name) for name in folder_names]
    _log.debug("final folder names %r", folder_names)

    album_folder = os.path.join(
        library_root, *_subroot_trie(section, metadata), *folder_names)

    # doesn't work as expected for external media
    #os.makedirs(album_folder, exist_ok=True)
    subprocess.check_call(["mkdir", "-p", album_folder])

    _log.info("using album folder %r", album_folder)
    return album_folder


def _generate_basename(section, metadata):
    """Build the filename for a track's FLAC or MP3 file.

    :arg str section: "FLAC" or "MP3"
    :arg dict metadata: the finalized metadata for a single track
    :return: a relative file name
    :rtype: :obj:`str`

    """
    _log.call(section, metadata)

    config = get_config()

    ndisc = "ndisc_" if metadata["album_disctotal"] > 1 else ""
    is_compilation = metadata["album_compilation"]
    track_format_spec = (
        config[section][ndisc + "track_filename"] if not is_compilation
        else config[section][ndisc + "compilation_track_filename"])
    _log.debug("using template %r", track_format_spec)

    basename = track_format_spec.format(**metadata)
    _log.debug("raw basename %r", basename)

    if config[section].getboolean("use_xplatform_safe_names"):
        # paranoid-safe and compact, but less readable
        basename = _xplatform_safe(
            basename, fileext=config[section]["track_fileext"])
    else:
        # as close to format spec as possible, but still relatively safe
        basename = re.sub(r"[^0-9a-zA-Z-.,_() ]", '_', basename)
    _log.debug("final basename %r", basename)

    track_filename = basename + config[section]["track_fileext"]
    _log.info("using track filename %r", track_filename)

    return track_filename


def _xplatform_safe(path, fileext=""):
    """Transform *path* so that it is safe to use across platforms.

    :arg path:
       a :obj:`list` of folder names, or a file basename
    :keyword str fileext:
       if *path* is a file basename, this is the file extension that
       will be appended to form the complete file name
    :return: the transformed *path*
    :rtype: the same type as *path* (:obj:`list` or :obj:`str`)

    """
    _log.call(path, fileext=fileext)

    safe_names = path if type(path) is list else [path]
    for (pattern, replacement) in [
            (r"\s+", '-'), # contiguous ws to '-'
            (r"[^0-9a-zA-Z-.,_]+", '_'), # contiguous special to '_'
            (r"^[^0-9a-zA-Z_]", '_'), # non-alphanum/underscore at [0] to '_'
            (r"([-.,_]){2,}", r'\1') # 2+ contiguous special/replacement to \1
            ]:
        safe_names = [
            re.sub(pattern, replacement, name) for name in safe_names]

    # can't know the target file system ahead of time, so assume 255 UTF-8
    # bytes as the "least common denominator" limit for all path components
    safe_names = [
        name.encode()[:255 - len(fileext)].decode(errors="ignore")
        for name in safe_names]

    transformed = safe_names if type(path) is list else safe_names[0]

    _log.debug("%r -> %r", path, transformed)
    return transformed


def _subroot_trie(section, metadata):
    """Build zero or more subdirectories below the library root to form
    an easily navigable "trie" structure for audio files.

    :arg str section: "FLAC" or "MP3"
    :arg dict metadata: the finalized metadata for a single track
    :return:
       a list (possibly empty) of directory names that form a trie
       structure for organizing audio files

    """
    _log.call(metadata)

    config = get_config()

    key = (
        config[section]["library_subroot_trie_key"]
        if not metadata["album_compilation"] else
        config[section]["library_subroot_compilation_trie_key"])
    level = config[section].getint("library_subroot_trie_level")

    # to skip building a directory trie structure, the key can be left empty or
    # the level can be set to zero (0)
    if not key or level <= 0:
        _log.trace("RETURN []")
        return []

    term = metadata[key]

    # issues/3
    trie_ignore_leading_article = config[section].get(
        "trie_ignore_leading_article", "")
    if trie_ignore_leading_article:
        articles = trie_ignore_leading_article.upper().split()
        words = term.split()
        if words[0].upper() in articles:
            # do not simply join on space; remaining white space may be
            # significant (e.g. NIN "THE S L  I   P" -> "S L  I   P")
            term = term[len(words[0]):].lstrip()

    term = re.sub(r"[^0-9a-zA-Z]", "", term).upper()
    # use len(term) - 1 so trie prefixes never include the full term
    nodes = [term[:n + 1] for n in range(min(level, len(term) - 1))]
    # edge case - any non-alphanumeric key falls into the special '_' node
    if not nodes:
        nodes = ['_']

    _log.return_(nodes)
    return nodes


def _styled(widget, **options):
    """Apply `Ttk Styling
    <https://docs.python.org/3/library/tkinter.ttk.html#ttkstyling>`_ to
    *widget*.

    :arg widget: any Ttk widget
    :arg dict options: the styling options for *widget*
    :return: *widget* with styling applied

    """
    style_id = "%x.%s" % (id(widget), widget.winfo_class())
    Style().configure(style_id, **options)
    widget.config(style=style_id)

    return widget


#: The text content of the dialog that explains FLACManager's
#: prerequisites.
_PREREQUISITES_TEXT = """\
FLACManager runs on Python 3.3+ compiled against Tk 8.5+.

The following EXTERNAL software components are also required:

* libdiscid (http://musicbrainz.org/doc/libdiscid)
* flac (http://flac.sourceforge.net/)
* lame (http://lame.sourceforge.net/)
* diskutil (Mac OS X command line utility)
* open (Mac OS X command line utility)
* mkdir (Mac OS X command line utility)

The flac and lame command line binaries must be available on
your $PATH, and the location of the libdiscid library must be
specified in the flacmanager.ini configuration file.

The libdiscid, flac and lame components can be easily installed
from MacPorts (http://www.macports.org/).

Finally, You MUST register for a Gracenote developer account in
order for FLACManager's metadata aggregation to work properly:

1. Create your Gracenote developer account at
   https://developer.gracenote.com/.
2. Create an app named "FLACManager".
3. Save your Gracenote Client ID in the flacmanager.ini
   configuration file.

(FLACManager will automatically obtain and store your Gracenote
User ID in the flacmanager.ini file.)"""


class _TextDialog(simpledialog.Dialog):
    """A dialog that presents a simple formatted text message."""

    def __init__(self, master, text, **options):
        """
        :arg master: the parent object of the dialog
        :arg str text: the text to display in the dialog
        :arg dict options: configuration options for the dialog

        *text* should contain line breaks at positions desirable for
        on-screen display. The total number of lines will determine the
        height of the text widget,

        *options* are passed directly to the parent
        :class:`tkinter.simpledialog.Dialog` class initializer.

        """
        self._text = text # must be set BEFORE calling super __init__!
        super().__init__(master, **options)

    def body(self, frame):
        """Create the content of the dialog."""
        with StringIO(self._text) as f:
            lines = f.readlines()

        text = Text(frame, bd=0, relief=FLAT, height=len(lines), wrap=WORD)
        text.insert(END, "".join(lines))
        text.config(state=DISABLED)
        text.pack()

    def buttonbox(self):
        """Create the button to dismiss the dialog."""
        box = Frame(self)
        Button(
            box, text="OK", width=11, command=self.ok,
            default=ACTIVE).pack(padx=_PADX, pady=_PADY)
        self.bind("<Return>", self.ok)
        box.pack()


class _EditConfigurationDialog(simpledialog.Dialog):
    """Base class for dialogs that allow the user to edit the
    *flacmanager.ini* configuration file.

    """

    def body(self, frame):
        """Create the content of the dialog.

        :arg Frame frame: the frame that contains the body content

        """
        self._row = 0
        self._variables = {} # "Section" -> { "option" -> tk-variable }

        self._populate(frame, get_config())

        frame.grid_columnconfigure(0, weight=0)

    def section(self, parent, section_name):
        """Add a section header to the body of the dialog.

        :arg parent: the parent object of the section header
        :arg str section_name: the name of the configuration section

        """
        section_label = _styled(
            Label(parent, text=section_name), font="-size 13 -weight bold")
        section_label.grid(row=self._row, columnspan=2, pady=11, sticky=W)

        self._row += 1

    def option(
            self, parent, section_name, option_name, value, width=67):
        """Add an editable option to the body of the dialog.

        :arg parent: the parent object of the section header
        :arg str section_name: the name of the configuration section
        :arg str option_name: the name of the configuration option
        :arg value: the default/initial value of the option
        :keyword int width:
           the display width of the entry box for this option's value

        """
        Label(parent, text=option_name).grid(row=self._row, sticky=E)

        if type(value) is int:
            variable = IntVar(self, value=value)
        elif type(value) is bool:
            variable = BooleanVar(self, value=value)
        elif type(value) is float:
            variable = DoubleVar(self, value=value)
        else:
            variable = StringVar(
                self, value=value if type(value) is not list else value[0])

        if type(value) is list:
            widget = OptionMenu(parent, variable, *value[1:])
        elif type(variable) is BooleanVar:
            widget = Checkbutton(
                parent, variable=variable, onvalue=True, offvalue=False)
        else:
            widget = Entry(parent, textvariable=variable, width=width)
        widget.grid(row=self._row, column=1, sticky=W, padx=_PADX)

        self._row += 1

        # track the variable; see apply()
        if section_name not in self._variables:
            self._variables[section_name] = {}
        self._variables[section_name][option_name] = variable

    def buttonbox(self):
        """Create the buttons to save and/or dismiss the dialog."""
        box = Frame(self)

        Button(
            box, text="Save", width=10, command=self.ok, default=ACTIVE
            ).pack(side=LEFT, padx=_PADX, pady=_PADY)

        Button(
            box, text="Cancel", width=10,
            command=self.cancel
            ).pack(side=LEFT, padx=_PADX, pady=_PADY)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack()

    def apply(self):
        """Save changes to the *flacmanager.ini* file."""
        with _CONFIG_LOCK:
            config = get_config()

            for (section, optvar) in self._variables.items():
                for (option, variable) in optvar.items():
                    # values MUST be strings!
                    if type(variable) is not BooleanVar:
                        config[section][option] = str(variable.get())
                    else:
                        config[section][option] = \
                            "yes" if variable.get() else "no"

            save_config()


class EditRequiredConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit **required** options from
    the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("Organize")
        option(
            "Organize", "library_root", config["Organize"]["library_root"])

        section("Gracenote")
        option("Gracenote", "client_id", config["Gracenote"]["client_id"])

        section("MusicBrainz")
        option(
            "MusicBrainz", "contact_url_or_email", 
            config["MusicBrainz"]["contact_url_or_email"])
        option(
            "MusicBrainz", "libdiscid_location",
            config["MusicBrainz"]["libdiscid_location"])


class EditAggregationConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit metadata aggregation
    options from the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("HTTP")
        option("HTTP", "timeout", config["HTTP"].getfloat("timeout"), width=7)

        section("Gracenote")
        option("Gracenote", "client_id", config["Gracenote"]["client_id"])
        option("Gracenote", "user_id", config["Gracenote"]["user_id"])

        section("MusicBrainz")
        option(
            "MusicBrainz", "contact_url_or_email", 
            config["MusicBrainz"]["contact_url_or_email"])
        option(
            "MusicBrainz", "libdiscid_location",
            config["MusicBrainz"]["libdiscid_location"])


class EditOrganizationConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit library folder/file
    organization options from the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("Organize")
        option("Organize", "library_root", config["Organize"]["library_root"])
        option(
            "Organize", "library_subroot_trie_key",
            config["Organize"]["library_subroot_trie_key"], width=29)
        option(
            "Organize", "library_subroot_trie_level",
            config["Organize"].getint("library_subroot_trie_level"), width=5)
        option(
            "Organize", "album_folder",
            config["Organize"].get("album_folder", raw=True))
        option(
            "Organize", "ndisc_album_folder",
            config["Organize"].get("ndisc_album_folder", raw=True))
        option(
            "Organize", "compilation_album_folder",
            config["Organize"].get("compilation_album_folder", raw=True))
        option(
            "Organize", "ndisc_compilation_album_folder",
            config["Organize"].get("ndisc_compilation_album_folder", raw=True))
        option(
            "Organize", "track_filename",
            config["Organize"].get("track_filename", raw=True))
        option(
            "Organize", "ndisc_track_filename",
            config["Organize"].get("ndisc_track_filename", raw=True))
        option(
            "Organize", "compilation_track_filename",
            config["Organize"].get("compilation_track_filename", raw=True))
        option(
            "Organize", "ndisc_compilation_track_filename",
            config["Organize"].get("ndisc_compilation_track_filename", raw=True))
        option(
            "Organize", "use_xplatform_safe_names",
            config["Organize"].getboolean("use_xplatform_safe_names"))


class EditFLACEncodingConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit FLAC encoding options from
    the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("FLAC")
        option(
            "FLAC", "flac_encode_options",
            config["FLAC"]["flac_encode_options"])


class EditVorbisCommentsConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit default FLAC Vorbis comment
    options from the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame, width=29)

        section("Vorbis")

        for comment in config["Vorbis"].keys():
            option("Vorbis", comment, config["Vorbis"].get(comment, raw=True))


class EditFLACOrganizationConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit FLAC folder/file
    organization options from the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("FLAC")
        option(
            "FLAC", "library_root",
            config["FLAC"].get("library_root", raw=True))
        option(
            "FLAC", "library_subroot_trie_key",
            config["FLAC"].get("library_subroot_trie_key", raw=True), width=29)

        # there won't be any validation if this is set to a non-interpolated,
        # non-int value!
        option(
            "FLAC", "library_subroot_trie_level",
            config["FLAC"].get("library_subroot_trie_level", raw=True))
        Label(
            frame, text="${Organize:library_subroot_trie_level} or a number"
            ).grid(row=self._row, column=1, sticky=W)
        self._row += 1

        option(
            "FLAC", "album_folder",
            config["FLAC"].get("album_folder", raw=True))
        option(
            "FLAC", "ndisc_album_folder",
            config["FLAC"].get("ndisc_album_folder", raw=True))
        option(
            "FLAC", "compilation_album_folder",
            config["FLAC"].get("compilation_album_folder", raw=True))
        option(
            "FLAC", "ndisc_compilation_album_folder",
            config["FLAC"].get("ndisc_compilation_album_folder", raw=True))
        option(
            "FLAC", "track_filename",
            config["FLAC"].get("track_filename", raw=True))
        option(
            "FLAC", "ndisc_track_filename",
            config["FLAC"].get("ndisc_track_filename", raw=True))
        option(
            "FLAC", "compilation_track_filename",
            config["FLAC"].get("compilation_track_filename", raw=True))
        option(
            "FLAC", "ndisc_compilation_track_filename",
            config["FLAC"].get("ndisc_compilation_track_filename", raw=True))

        # there won't be any validation if this is set to a non-interpolated,
        # non-boolean ("yes"/"no") value!
        option(
            "FLAC", "use_xplatform_safe_names",
            config["FLAC"].get("use_xplatform_safe_names", raw=True))
        Label(
            frame, text="${Organize:use_xplatform_safe_names}, yes, or no"
            ).grid(row=self._row, column=1, sticky=W)
        self._row += 1


class EditMP3EncodingConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit MP3 encoding options from
    the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("MP3")
        option(
            "MP3", "lame_encode_options", config["MP3"]["lame_encode_options"])


class EditID3v2TagsConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit default MP3 ID3v2 tag
    options from the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame, width=29)

        section("ID3v2")

        for tag in config["ID3v2"].keys():
            option("ID3v2", tag, config["ID3v2"].get(tag, raw=True))


class EditMP3OrganizationConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit MP3 folder/file
    organization options from the *flacmanager.ini* configuration file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("MP3")
        option(
            "MP3", "library_root",
            config["MP3"].get("library_root", raw=True))
        option(
            "MP3", "library_subroot_trie_key",
            config["MP3"].get("library_subroot_trie_key", raw=True), width=29)

        # there won't be any validation if this is set to a non-interpolated,
        # non-int value!
        option(
            "MP3", "library_subroot_trie_level",
            config["MP3"].get("library_subroot_trie_level", raw=True))
        Label(
            frame, text="${Organize:library_subroot_trie_level} or a number"
            ).grid(row=self._row, column=1, sticky=W)
        self._row += 1

        option(
            "MP3", "album_folder",
            config["MP3"].get("album_folder", raw=True))
        option(
            "MP3", "ndisc_album_folder",
            config["MP3"].get("ndisc_album_folder", raw=True))
        option(
            "MP3", "compilation_album_folder",
            config["MP3"].get("compilation_album_folder", raw=True))
        option(
            "MP3", "ndisc_compilation_album_folder",
            config["MP3"].get("ndisc_compilation_album_folder", raw=True))
        option(
            "MP3", "track_filename",
            config["MP3"].get("track_filename", raw=True))
        option(
            "MP3", "ndisc_track_filename",
            config["MP3"].get("ndisc_track_filename", raw=True))
        option(
            "MP3", "compilation_track_filename",
            config["MP3"].get("compilation_track_filename", raw=True))
        option(
            "MP3", "ndisc_compilation_track_filename",
            config["MP3"].get("ndisc_compilation_track_filename", raw=True))

        # there won't be any validation if this is set to a non-interpolated,
        # non-boolean ("yes"/"no") value!
        option(
            "MP3", "use_xplatform_safe_names",
            config["MP3"].get("use_xplatform_safe_names", raw=True))
        Label(
            frame, text="${Organize:use_xplatform_safe_names}, yes, or no"
            ).grid(row=self._row, column=1, sticky=W)
        self._row += 1


class EditUserInterfaceConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit user interface settings
    from the *flacmanager.ini* file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("UI")
        option(
            "UI", "minwidth", config["UI"].getint("minwidth", 1024), width=5)
        option(
            "UI", "minheight", config["UI"].getint("minheight", 768), width=5)
        option("UI", "padx", config["UI"].getint("padx", 7), width=3)
        option("UI", "pady", config["UI"].getint("pady", 7), width=3)
        option(
            "UI", "encoding_max_visible_tracks",
            config["UI"].getint("encoding_max_visible_tracks", 29), width=3)


class EditLoggingConfigurationDialog(_EditConfigurationDialog):
    """A dialog that allows the user to edit logging and debug settings
    from the *flacmanager.ini* file.

    """

    def _populate(self, frame, config):
        """Create the content of the dialog."""
        section = partial(self.section, frame)
        option = partial(self.option, frame)

        section("Logging")
        levels = [
            "TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        level = config["Logging"].get("level", "INFO")
        option(
            "Logging", "level",
            [level if level in levels else "INFO"] + levels)
        option("Logging", "filename", config["Logging"]["filename"])
        option("Logging", "filemode", config["Logging"]["filemode"])
        option("Logging", "format", config["Logging"].get("format", raw=True))

        section("HTTP")
        option(
            "HTTP", "debuglevel", config["HTTP"].getint("debuglevel", 0),
            width=3)


#: A list of recommended and proposed Vorbis comment names.
_VORBIS_COMMENTLIST = [
    # https://xiph.org/vorbis/doc/v-comment.html
    # [1] https://wiki.xiph.org/Field_names
    # [2] http://age.hobba.nl/audio/mirroredpages/ogg-tagging.html
    # [3] http://reallylongword.org/vorbiscomment/
    "ALBUM (collection name)",
    "ARRANGER (who arranged the piece)", # [2]
    "ARTIST (artist responsible for the work)",
    "AUTHOR (author of spoken work)", # [2]
    "CATALOGNUMBER (producer/label catalog number)", # [3]
    "COMMENT (user comment)", # [2]
    "COMPOSER (composer of the work)", # [1]
    "CONDUCTOR (conductor of the work)", # [2]
    "CONTACT (creator/distributor contact info)",
    "COPYRIGHT (Copyright attribution)",
    "DATE (recording date)",
    "DESCRIPTION (description of contents)",
    "DISCNUMBER (multi-disc number)", # [1]
    "DISCTOTAL (number of discs)", # [1]
    #"ENCODER (user application name)", # [1]
    "ENCODING (quality/bitrate settings)", # [2]
    "ENGINEER (who produced the master)", # [3]
    "ENSEMBLE (group playing the piece)", # [2]
    "GENRE (music genre)",
    "GUESTARTIST (collaborative artist)", # [3]
    "ISRC (International Standard Recording Code)",
    "LABEL (record label or imprint)", # [2]
    "LICENSE (License information)",
    "LOCATION (recording location)",
    "LYRICIST (who wrote the lyrics)", # [2]
    "OPUS (number of the work)", # [2]
    "ORGANIZATION (production organization)",
    "PART (division within a work)", # [2]
    "PARTNUMBER (number of the division/part)", # [2]
    "PERFORMER (artist(s) who performed the work)",
    "PRODUCER (producer of the work)", # [3]
    "PRODUCTNUMBER (UPC, EAN, JAN, etc.)", # [1]
    "PUBLISHER (who published the disc)", # [2]
    "REMIXER (who remixed the work)", # [3]
    "SOURCEARTIST (original artist of performed work)", # [3]
    "SOURCEMEDIA (recording media type)", # [1]
    "TITLE (Track/Work name)",
    "TRACKNUMBER (number of this piece)",
    "TRACKTOTAL (number of tracks)", # [1]
    "VERSION (differentiate multiple versions)",
    "VOLUME (multi-volume work)", # [3]
]


#: A list of standard and common non-standard ID3v2 frames.
_ID3V2_TAGLIST = [
    # http://id3.org/id3v2.3.0 (unless otherwise noted)
    "AENC (Audio encryption)",
    "APIC (Attached picture)",
    "COMM (Comments)",
    "COMR (Commercial frame)",
    "ENCR (Encryption method registration)",
    "EQUA (Equalization)",
    "ETCO (Event timing codes)",
    "GEOB (General encapsulated object)",
    "GRID (Group identification registration)",
    "IPLS (Involved people list)",
    "LINK (Linked information)",
    "MCDI (Music CD identifier)",
    "MLLT (MPEG location lookup table)",
    "OWNE (Ownership frame)",
    "PRIV (Private frame)",
    "PCNT (Play counter)",
    "POPM (Popularimeter)",
    "POSS (Position synchronisation frame)",
    "RBUF (Recommended buffer size)",
    "RVAD (Relative volume adjustment)",
    "RVRB (Reverb)",
    "SYLT (Synchronized lyric/text)",
    "SYTC (Synchronized tempo codes)",
    "TALB (Album/Movie/Show title)",
    "TBPM (BPM (beats per minute))",
    "TCMP (iTunes Compilation Flag)", # http://id3.org/iTunes Compilation Flag
    "TCOM (Composer)",
    "TCON (Content type)",
    "TCOP (Copyright message)",
    "TDAT (Date)",
    "TDLY (Playlist delay)",
    #"TENC (Encoded by)",
    "TEXT (Lyricist/Text writer)",
    "TFLT (File type)",
    "TIME (Time)",
    "TIT1 (Content group description)",
    "TIT2 (Title/songname/content description)",
    "TIT3 (Subtitle/Description refinement)",
    "TKEY (Initial key)",
    "TLAN (Language(s))",
    "TLEN (Length)",
    "TMED (Media type)",
    "TOAL (Original album/movie/show title)",
    "TOFN (Original filename)",
    "TOLY (Original lyricist(s)/text writer(s))",
    "TOPE (Original artist(s)/performer(s))",
    "TORY (Original release year)",
    "TOWN (File owner/licensee)",
    "TPE1 (Lead performer(s)/Soloist(s))",
    "TPE2 (Band/orchestra/accompaniment)",
    "TPE3 (Conductor/performer refinement)",
    "TPE4 (Interpreted, remixed, or otherwise modified by)",
    "TPOS (Part of a set)",
    "TPUB (Publisher)",
    "TRCK (Track number/Position in set)",
    "TRDA (Recording dates)",
    "TRSN (Internet radio station name)",
    "TRSO (Internet radio station owner)",
    "TSIZ (Size)",
    "TSOT (iTunes Title Sort)", # http://id3.org/iTunes
    "TSOP (iTunes Artist Sort)", # http://id3.org/iTunes
    "TSOA (iTunes Album Sort)", # http://id3.org/iTunes
    "TSO2 (iTunes Album Artist Sort)", # http://id3.org/iTunes
    "TSOC (iTunes Composer Sort)", # http://id3.org/iTunes
    "TSRC (ISRC (international standard recording code))",
    "TSSE (Software/Hardware and settings used for encoding)",
    "TYER (Year)",
    "TXXX (User defined text information frame)",
    "UFID (Unique file identifier)",
    "USER (Terms of use)",
    "USLT (Unsychronized lyric/text transcription)",
    "WCOM (Commercial information)",
    "WCOP (Copyright/Legal information)",
    "WOAF (Official audio file webpage)",
    "WOAR (Official artist/performer webpage)",
    "WOAS (Official audio source webpage)",
    "WORS (Official internet radio station homepage)",
    "WPAY (Payment)",
    "WPUB (Publishers official webpage)",
    "WXXX (User defined URL link frame)",
]


@logged
class EditCustomMetadataTaggingDialog(simpledialog.Dialog):
    """Base dialog that allows the user to add/change/remove custom
    metadata fields for Vorbis/ID3v2 tagging.

    """

    class _TaggingHelp(simpledialog.Dialog):
        """Dialog that displays a list of known and recommended Vorbis
        comment names **or** standard and common non-standard ID3v2
        frames.

        """
    
        _VORBIS_HELP_TEXT = None

        _ID3V2_HELP_TEXT = None

        @classmethod
        @lru_cache(maxsize=2)
        def _helptext(cls, type_):
            """Format the list of Vorbis comments or ID3v2 frames.

            :arg str type_:
               either "Vorbis comment" or "ID3v2 tag"
            :return: a text listing of Vorbis comments or ID3v2 frames
            :rtype: :obj:`str`

            """
            if type_ == "Vorbis comment":
                if cls._VORBIS_HELP_TEXT is None:
                    cls._VORBIS_HELP_TEXT = '\n'.join(_VORBIS_COMMENTLIST)
                text = cls._VORBIS_HELP_TEXT
            else: # ID3v2 tag
                if cls._ID3V2_HELP_TEXT is None:
                    cls._ID3V2_HELP_TEXT = '\n'.join(_ID3V2_TAGLIST)
                text = cls._ID3V2_HELP_TEXT

            return text

        def __init__(self, master, type_):
            """
            :arg master: the parent object of this dialog
            :arg str type_:
               either "Vorbis comment" or "ID3v2 tag"

            """
            self._type = type_ # must be set BEFORE calling super __init__!
            super().__init__(master, title="%s help" % type_)

        def body(self, frame):
            """Populate this dialog."""
            help_list = scrolledtext.ScrolledText(
                frame, height=17, bd=0, relief=FLAT, wrap=NONE)
            help_list.insert(END, self._helptext(self._type))
            help_list.config(state=DISABLED)
            help_list.pack(
                side=LEFT, fill=BOTH, expand=YES, padx=_PADX, pady=_PADY)

    def __init__(self, master, metadata, **keywords):
        """Initialize the dialog.

        :arg master: the parent object of this dialog
        :arg metadata: the album or track metadata
        :arg dict keywords:
           *name=value* keywords used to configure this dialog

        """
        self.__log.call(master, metadata, **keywords)

        self._metadata = metadata
        self._fields = [] # (vorbis_var, id3v2_var, value_var)
        self._widgets = [] # (button, vorbis_entry, id3v2_entry, value_entry)
        # must come last, as it will call body(frame) before returning!
        super().__init__(master, **keywords)

    def body(self, frame):
        """Create the content of the dialog.

        :arg Frame frame: the frame that contains the body content

        """
        self.__log.call(frame)

        self._row = 0

        self._instructions_var = StringVar()
        self._body_instructions()

        Label(
            frame, textvariable=self._instructions_var, anchor=NW
        ).grid(row=self._row, columnspan=4, pady=7, sticky=W)
        self._row += 1

        add_button = _styled(
            Button(
                frame, text="Add", default=ACTIVE,
                command=lambda f=self._add_field, p=frame: f(p)),
            foreground="Blue")
        add_button.grid(row=self._row, column=0, pady=7, sticky=W)

        vorbis_frame = Frame(frame)

        vorbis_label = _styled(
            Label(vorbis_frame, text="Vorbis"), font="-weight bold")
        vorbis_label.pack(side=LEFT)

        vorbis_help_button = Button(
            vorbis_frame, text='?', width=1,
            command=
                lambda self=self:
                    self._TaggingHelp(vorbis_frame, type_="Vorbis comment"))
        vorbis_help_button.pack(side=LEFT)

        vorbis_frame.grid(row=self._row, column=1, pady=_PADY, sticky=W)

        id3v2_frame = Frame(frame)

        id3v2_label = _styled(
            Label(id3v2_frame, text="ID3v2"), font="-weight bold")
        id3v2_label.pack(side=LEFT)

        id3v2_help_button = Button(
            id3v2_frame, text='?', width=1,
            command=
                lambda self=self:
                    self._TaggingHelp(id3v2_frame, type_="ID3v2 tag"))
        id3v2_help_button.pack(side=LEFT)

        id3v2_frame.grid(row=self._row, column=2, pady=_PADY, sticky=W)

        value_label = _styled(Label(frame, text="Value"), font="-weight bold")
        value_label.grid(row=self._row, column=3, pady=_PADY, sticky=W)

        self._row += 1

        for ((vorbis_comment, id3v2_tag), values) \
                in self._metadata.get("__custom", {}).items():
            self._add_field(
                frame, vorbis_comment=vorbis_comment, id3v2_tag=id3v2_tag,
                values=values)

    def _body_instructions(self):
        """Populate text instructions for the dialog."""
        self._instructions_var.set(
            "Specify a Vorbis comment and/or ID3v2 tag name, and a value.\n"
            "Changes to metadata are not saved unless the [Save] button is "
            "clicked.\n"
            "Fields with empty comment/tag names, or an empty value, are NOT "
            "saved.\n"
            "Specify multiple values by adding multiple fields with the same "
            "comment and/or tag and a different value."
        )

    def _add_field(self, parent, vorbis_comment="", id3v2_tag="", values=None):
        """Render a custom field in the dialog body.

        :arg parent: the object that contains the field controls
        :keyword str vorbis_comment: the custom Vorbis comment
        :keyword str id3v2_tag: the custom ID3v2 tag
        :keyword list values: the value(s) for the custom comment/tag

        """
        self.__log.call(
            parent, vorbis_comment=vorbis_comment, id3v2_tag=id3v2_tag,
            values=values)

        if values is None:
            values = [""]

        for value in values:
            # len(self._fields) will be the index where references to the
            # variables are stored
            fields_ix = len(self._fields)

            clear_button = _styled(
                Button(
                    parent, text="\u00d7", width=1,
                    command=lambda f=self._clear_field, ix=fields_ix: f(ix)),
                foreground="Red", font="-weight bold")
            clear_button.grid(row=self._row, column=0)

            vorbis_var = StringVar(parent, value=vorbis_comment)
            vorbis_entry = Entry(parent, textvariable=vorbis_var, width=17)
            vorbis_entry.grid(row=self._row, column=1, sticky=W)

            id3v2_var = StringVar(parent, value=id3v2_tag)
            id3v2_entry = Entry(parent, textvariable=id3v2_var, width=7)
            id3v2_entry.grid(row=self._row, column=2, sticky=W)

            value_var = StringVar(parent, value=value)
            value_entry = Entry(parent, textvariable=value_var, width=59)
            value_entry.grid(row=self._row, column=3, sticky=W)

            self._fields.append((vorbis_var, id3v2_var, value_var))
            self._widgets.append(
                (clear_button, vorbis_entry, id3v2_entry, value_entry))

            self._row += 1

    def _clear_field(self, index):
        """Clear (effectively removing) the *index* -th field.

        :arg int index: index into the *fields* list of variables

        """
        self.__log.call(index)

        for var in self._fields[index]:
            var.set("")
        for i in range(4):
            self._widgets[index][i].destroy()
            

    def buttonbox(self):
        """Create the buttons to save and/or dismiss the dialog."""
        box = Frame(self)

        Button(box, text="Save", width=10, command=self.ok).pack(
            side=LEFT, padx=_PADX, pady=_PADY)

        Button(box, text="Cancel", width=10, command=self.cancel).pack(
            side=LEFT, padx=_PADX, pady=_PADY)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack()

    def apply(self):
        """Save changes to *metadata*."""
        self.__log.call()

        custom = self._metadata["__custom"] = OrderedDict()

        for (vorbis_var, id3v2_var, value_var) in self._fields:
            vorbis_comment = vorbis_var.get()
            id3v2_tag = id3v2_var.get()
            value = value_var.get()

            if value and (vorbis_comment or id3v2_tag):
                key = (vorbis_comment, id3v2_tag)

                if key not in custom:
                    custom[key] = [value]
                else:
                    custom[key].append(value)

                self.__log.info("saved custom %r = %r", key, value)
            elif vorbis_comment or id3v2_tag or value:
                self.__log.warning(
                    "ignoring (%r, %r) = %r", vorbis_comment, id3v2_tag, value)


@logged
class EditAlbumCustomMetadataTaggingDialog(EditCustomMetadataTaggingDialog):
    """Dialog that allows the user to add/change/remove custom metadata
    fields for *all tracks* (i.e. the album) at once for Vorbis/ID3v2
    tagging.

    """

    def __init__(self, master, album_metadata, tracks_metadata, **keywords):
        """Initialize the dialog.

        :arg master: the parent object of this dialog
        :arg album_metadata: the album metadata
        :arg tracks_metadata: the track metadata
        :arg dict keywords:
           *name=value* keywords used to configure this dialog

        """
        self.__log.call(master, album_metadata, tracks_metadata, **keywords)

        self._tracks_metadata = tracks_metadata
        self._cleared = set()
        # must come last, as it will call body(frame) before returning!
        super().__init__(master, album_metadata, **keywords)

    def _body_instructions(self):
        """Populate text instructions for the dialog."""
        super()._body_instructions()
        self._instructions_var.set(
            self._instructions_var.get() + '\n' +
            "Changes applied (saved) to custom metadata tagging fields at the "
            "album level are applied to ALL tracks."
        )

    def _add_field(self, parent, vorbis_comment="", id3v2_tag="", values=None):
        """Render a custom field in the dialog body.

        :arg parent: the object that contains the field controls
        :keyword str vorbis_comment: the custom Vorbis comment
        :keyword str id3v2_tag: the custom ID3v2 tag
        :keyword list values: the value(s) for the custom comment/tag

        If adding an already-populated field, the entry widgets will be
        disabled. This is a bit ugly, but it greatly simplifies change
        tracking - modify is modeled as a remove-then-add operation
        instead of having to keep track of old and new values.

        """
        self.__log.call(
            parent, vorbis_comment=vorbis_comment, id3v2_tag=id3v2_tag,
            values=values)

        super()._add_field(
            parent, vorbis_comment=vorbis_comment, id3v2_tag=id3v2_tag,
            values=values)

        if (vorbis_comment or id3v2_tag) and values:
            for added in self._widgets[-len(values):]:
                for widget in added[1:]:
                    widget.config(state=DISABLED)

    def _clear_field(self, index):
        """Clear (effectively removing) the *index* -th field.

        :arg int index: index into the *fields* list of variables

        This method keeps track of which fields have been cleared so
        that the changes can be "replayed" for each track.

        """
        self.__log.call(index)

        key = (
            self._fields[index][0].get(), # Vorbis comment
            self._fields[index][1].get() # ID3v2 tag
        )
        value = self._fields[index][2].get() # value
        self._cleared.add((key, value))

        super()._clear_field(index)

    def apply(self):
        """Save changes to the album metadata.

        The changes will also be applied to all tracks.

        """
        self.__log.call()
        super().apply()

        album_custom = self._metadata["__custom"]

        for i in range(1, len(self._tracks_metadata)):
            track_custom = \
                self._tracks_metadata[i].setdefault("__custom", OrderedDict())

            self._replay_clear(track_custom, i)

            for (key, values) in album_custom.items():
                track_custom[key] = values
                self.__log.info(
                    "applied custom %r = %r to track %d", key, values, i)

    def _replay_clear(self, track_custom, i):
        """Clear the same fields in each track that were cleared in the
        album.

        :arg dict track_custom:
           the custom metadata tagging fields for track *i*
        :arg int i:
           the track number

        """
        self.__log.call(track_custom, i)

        if self._cleared:
            self.__log.debug("clearing %r from track %d", self._cleared, i)

        for (key, value) in self._cleared:
            track_values = track_custom.get(key)

            if track_values and value in track_values:
                track_values[:] = [v for v in track_values if v != value]
                self.__log.info(
                    "cleared %r = %r from track %d", key, value, i)

            if track_values == []:
                del track_custom[key]


def resolve_path(spec):
    """Evaluate all variables in *spec* and make sure it's absolute.

    :arg str spec: a directory or file path template
    :return: a valid, absolute file system path
    :rtype: :obj:`str`

    """
    _log.call(spec)

    resolved_path = os.path.realpath(
        os.path.abspath(
            os.path.expandvars(
                os.path.expanduser(spec))))

    if not os.path.exists(resolved_path):
        raise RuntimeError("not a valid path: " + resolved_path)

    _log.return_(resolved_path)
    return resolved_path


def encode_flac(
        cdda_filename, flac_filename, track_metadata, stdout_filename=None):
    """Rip a CDDA file to a tagged FLAC file.

    :arg str cdda_filename: absolute CD-DA file name
    :arg str flac_filename: absolute *.flac* file name
    :arg dict track_metadata: tagging fields for this track
    :keyword str stdout_filename:
       absolute file name for redirected stdout

    """
    _log.call(
        cdda_filename, flac_filename, track_metadata,
        stdout_filename=stdout_filename)

    command = ["flac"]
    command.extend(get_config().get("FLAC", "flac_encode_options").split())

    if track_metadata["album_cover"]:
        command.append("--picture=%s" % track_metadata["album_cover"])

    vorbis_comments = make_vorbis_comments(track_metadata)
    for (name, values) in vorbis_comments.items():
        if not values:
            continue
        command.extend(["--tag=%s=%s" % (name, value) for value in values])

    command.append("--output-name=%s" % flac_filename)
    command.append(cdda_filename)

    _log.info("command = %r", command)

    if stdout_filename:
        with open(stdout_filename, "wb") as f:
            subprocess.check_call(
                command, stdout=f, stderr=subprocess.STDOUT)
    else:
        subprocess.check_call(command)

    _log.info("finished %s", flac_filename)


def decode_wav(flac_filename, wav_filename, stdout_filename=None):
    """Convert a FLAC file to a WAV file.

    :arg str flac_filename: absolute *.flac* file name
    :arg str wav_filename: absolute *.wav* file name
    :keyword str stdout_filename:
       absolute file name for redirected stdout

    """
    _log.call(flac_filename, wav_filename, stdout_filename=stdout_filename)

    command = ["flac", "--decode"]
    command.extend(get_config().get("FLAC", "flac_decode_options").split())
    command.append("--output-name=%s" % wav_filename)
    command.append(flac_filename)

    _log.info("command = %r", command)

    if stdout_filename:
        with open(stdout_filename, "wb") as f:
            subprocess.check_call(
                command, stdout=f, stderr=subprocess.STDOUT)
    else:
        subprocess.check_call(command)

    _log.info("finished %s", wav_filename)


def encode_mp3(
        wav_filename, mp3_filename, track_metadata, scale=None,
        stdout_filename=None):
    """Convert a WAV file to an MP3 file.

    :arg str wav_filename: absolute *.wav* file name
    :arg str mp3_filename: absolute *.mp3* file name
    :arg dict track_metadata: tagging fields for this track
    :keyword float scale:
      multiply PCM data by this factor
    :keyword str stdout_filename:
       absolute file name for redirected stdout

    """
    _log.call(
        wav_filename, mp3_filename, track_metadata, scale=scale,
        stdout_filename=stdout_filename)

    command = ["lame"]
    command.extend(get_config()["MP3"]["lame_encode_options"].split())
    if scale is not None:
        command.extend(["--scale", "%.2f" % scale])
    command.append("--id3v2-only")

    if track_metadata["album_cover"]:
        command.extend(["--ti", track_metadata["album_cover"]])

    id3v2_tags = make_id3v2_tags(track_metadata)
    id3v2_utf16_tags = []
    for (name, values) in id3v2_tags.items():
        if not values:
            continue

        # ID3v2 spec calls for '/' separator, but iTunes only handles ','
        # separator correctly
        tag = "%s=%s" % (name, ", ".join(values))

        try:
            tag.encode("latin-1")
        except UnicodeEncodeError:
            id3v2_utf16_tags.extend(["--tv", tag])
        else:
            command.extend(["--tv", tag])

    # add any UTF-16 tags
    if id3v2_utf16_tags:
        command.append("--id3v2-utf16")
        command.extend(id3v2_utf16_tags)

    command.append(wav_filename)
    command.append(mp3_filename)

    _log.info("command = %r", command)

    if stdout_filename:
        with open(stdout_filename, "wb") as f:
            subprocess.check_call(
                command, stdout=f, stderr=subprocess.STDOUT)
    else:
        subprocess.check_call(command)

    _log.debug("finished %s", mp3_filename)


def make_vorbis_comments(metadata):
    """Create Vorbis comments for tagging from *metadata*.

    :arg dict metadata: the metadata for a single track
    :return: Vorbis comment name/value pairs
    :rtype: :obj:`dict`

    .. seealso::

       `Ogg Vorbis I format specification: comment field and header specification <https://xiph.org/vorbis/doc/v-comment.html>`_
          The only (?) "official recommendation" for Vorbis comments

       `Xiph Wiki: VorbisComment <https://wiki.xiph.org/VorbisComment>`_
          a (very) basic metadata format

       `Xiph Wiki: Field_names <https://wiki.xiph.org/Field_names>`_
          official (?) proposed updates to the VorbisComment recommendations

       `Ogg Vorbis Comment Field Recommendations <http://age.hobba.nl/audio/mirroredpages/ogg-tagging.html>`_
          Just a proposal, but linked directly from Xiph Wiki

    """
    _log.call(metadata)

    comments = _make_tagging_map("Vorbis", metadata)

    # only use COMPILATION=1
    if comments["COMPILATION"] == ['0']:
        del comments["COMPILATION"]

    # flac automatically includes a vendor string to identify itself
    comments["ENCODER"] = [
        "http://ninthtest.info/flac-mp3-audio-manager/ %s" % __version__]

    _log.return_(comments)
    return comments


def make_id3v2_tags(metadata):
    """Create ID3v2 frames for tagging from *metadata*.

    :arg dict metadata: the metadata for a single track
    :return: ID3v2 frame name/value pairs
    :rtype: :obj:`dict`

    .. seealso::

       `ID3 tag version 2.3.0 <http://id3.org/id3v2.3.0>`_
          The most compatible standard for ID3 tagging

       http://id3.org/iTunes
          ID3 tagging idiosyncracies in Apple iTunes

       `MusicBrainz Picard <http://picard.musicbrainz.org/>`_
          FLACManager does its best to get the tagging right the first
          time, but Picard is a fantastic post-encoding fixer-upper.

    """
    _log.call(metadata)

    tags = _make_tagging_map("ID3v2", metadata)

    # only use TCMP=1
    if tags["TCMP"] == ['0']:
        del tags["TCMP"]

    # lame automatically includes TSSE to identify itself
    tags["TENC"] = ["http://ninthtest.info/flac-mp3-audio-manager/"]

    _log.return_(tags)
    return tags


def _make_tagging_map(type_, metadata):
    """Create Vorbis comments or ID3v2 frames for tagging from
    *metadata*.

    :arg str type_:
       "Vorbis" or "ID3v2" (corresponds to a tagging section in the
       flacmanager.ini configuration file)
    :arg dict metadata: the metadata for a single track
    :return: Vorbis commen or ID3v2 frame name/value pairs
    :rtype: :obj:`dict`

    """
    _log.call(metadata)

    config = get_config()

    tags = OrderedDict()
    for (tag, spec) in config[type_].items():
        value = (
            spec.format(**metadata) if spec[0] == '{' # format specification
            else metadata[spec]) # direct key lookup

        # only include truthy values
        if value:
            tags[tag] = value if type(value) is list else [value]

    _update_custom_tagging(tags, type_, metadata)

    _log.return_(tags)
    return tags


def _update_custom_tagging(tags, type_, metadata):
    """Update *tags* with any custom Vorbis comments or ID3v2 tags from
    *metadata["__custom"]* (if defined).

    :arg dict tags: the tagging map for a track
    :arg str type_: "Vorbis" or "ID3v2"
    :arg dict metadata: the metadata for a single track

    *tags* is updated in place.
    
    .. note::
       Custom tags with the same name as a preconfigured tag will
       **replace** the preconfigured tag.

    """
    _log.call(metadata)

    if "__custom" in metadata:
        custom_tagpairs = []
        for ((vorbis_comment, id3v2_tag), values) \
                in metadata["__custom"].items():
            if type_ == "Vorbis" and vorbis_comment:
                custom_tagpairs.append((vorbis_comment, values))
            elif type_ == "ID3v2" and id3v2_tag:
                custom_tagpairs.append((id3v2_tag, values))
            else:
                _log.warning(
                    "skipping custom (%r, %r) = %r",
                    vorbis_comment, id3v2_tag, values)

        _log.debug(
            "custom tag pairs (before formatting):\n%r", custom_tagpairs)

        custom_tags = OrderedDict()
        for (tag, values) in custom_tagpairs:
            # custom values are always formatted, but only keep if non-empty
            values = [
                value for value in (spec.format(**metadata) for spec in values)
                if value]

            if values:
                if tag not in custom_tags:
                    custom_tags[tag] = values
                else:
                    custom_tags[tag].extend(values)

                _log.debug("custom %s %s = %r", type_, tag, custom_tags[tag])
            else:
                _log.warning(
                    "custom %s %s evaluated to an empty list", type_, tag)

        if custom_tags:
            _log.info("updating tagging map with %r", custom_tags)
            tags.update(custom_tags)


#: Used to pass data between a :class:`FLACEncoder` thread and the main
#: thread.
_ENCODING_QUEUE = queue.PriorityQueue()

#: The number of seconds to wait between enqueuing a FLAC encoding
#: status.
FLAC_ENCODING_STATUS_WAIT = 1.25


@logged
class FLACEncoder(threading.Thread):
    """A thread that rips CD-DA tracks to FLAC."""

    def __init__(self):
        """``FLACEncoder`` threads are daemonized so that they are
        killed automatically if the program exits.

        """
        self.__log.call()
        super().__init__(daemon=True)

        self._instructions = []

    def add_instruction(
            self, track_index, cdda_filename, flac_filename, mp3_filename,
            track_metadata):
        """Schedule a track for FLAC encoding.

        :arg int track_index: index (not ordinal) of the track
        :arg str cdda_filename: absolute CD-DA file name
        :arg str flac_filename: absolute *.flac* file name
        :arg str mp3_filename: absolute *.mp3* file name
        :arg dict track_metadata: tagging fields for this track

        """
        self.__log.call(
            track_index, cdda_filename, flac_filename, mp3_filename,
            track_metadata)

        self._instructions.append(
            (track_index, cdda_filename, flac_filename, mp3_filename,
                track_metadata))

    def run(self):
        """Rip CD-DA tracks to FLAC."""
        self.__log.call()

        mp3_encoder_threads = []
        for (index, cdda_fn, flac_fn, mp3_fn, metadata) in self._instructions:
            stdout_fn = make_tempfile(suffix=".out")

            # the FLAC encoding must block because it needs exclusive access to
            # the drive; so run the status updates in a separate thread
            status_interval_thread = threading.Thread(
                target=self._enqueue_status_interval,
                args=(index, cdda_fn, flac_fn, stdout_fn),
                daemon=True)
            status_interval_thread.start()

            flac_encoding_error = None
            try:
                encode_flac(
                    cdda_fn, flac_fn, metadata, stdout_filename=stdout_fn)
            except Exception as e:
                self.__log.exception("FLAC encoding failed")
                flac_encoding_error = e

            # touch the done file; see _enqueue_status_interval
            open("%s.done" % stdout_fn, 'w').close()

            # block until the status updates thread exits
            status_interval_thread.join()

            if flac_encoding_error is None:
                # run the MP3 encoding in a separate thread so we can move on
                # to the next CD-DA -> FLAC encoding; the MP3 encoder will
                # enqueue the "TRACK_COMPLETE" state when it's finished
                mp3_encoder = MP3Encoder(
                    index, cdda_fn, flac_fn, mp3_fn, stdout_fn, metadata)
                mp3_encoder.start()
                mp3_encoder_threads.append(mp3_encoder)
            else:
                status = (
                    index, cdda_fn, flac_fn, stdout_fn, flac_encoding_error)
                self.__log.error("enqueueing %r", status)
                _ENCODING_QUEUE.put((2, status))

        # make sure all MP3 encoders are done before enqueueing "FINISHED"
        for mp3_encoder_thread in mp3_encoder_threads:
            mp3_encoder_thread.join()

        status = (index, cdda_fn, flac_fn, stdout_fn, "FINISHED")
        self.__log.info("enqueueing %r", status)
        _ENCODING_QUEUE.put((13, status))

        # do not terminate until "FINISHED" status has been processed
        _ENCODING_QUEUE.join()

        self.__log.info("thread is exiting")

    def _enqueue_status_interval(
            self, track_index, cdda_filename, flac_filename,
            stdout_filename=None):
        """Enqueue a status update notification on an interval.

        :arg int track_index: index (**not** ordinal) of the track
        :arg str cdda_filename: absolute CD-DA file name
        :arg str flac_filename: absolute .flac file name
        :keyword str stdout_filename:
           absolute file name for redirected stdout

        .. note::
           This method is run in a separate thread (see :meth:`run`).

        """
        # enqueueing this status causes UI to read latest status line from the
        # stdout file
        status = (
            track_index, cdda_filename, flac_filename, stdout_filename,
            TRACK_ENCODING_FLAC)

        # when the FLAC encoding is complete, this file will be created whether
        # an error occurred or not
        done_filename = "%s.done" % stdout_filename

        self.__log.info(
            "enqueueing %r every %s seconds...",
            status, FLAC_ENCODING_STATUS_WAIT)

        # as long as the ".done" file doesn't exist, keep telling the UI to
        # read a status update from the stdout file
        exists = os.path.isfile
        while not exists(done_filename):
            _ENCODING_QUEUE.put((7, status))
            time.sleep(FLAC_ENCODING_STATUS_WAIT)


@logged
class MP3Encoder(threading.Thread):
    """A thread that converts WAV files to MP3 files."""

    def __init__(
            self, track_index, cdda_filename, flac_filename, mp3_filename,
            stdout_filename, track_metadata):
        """
        :arg int track_index: index (not ordinal) of the track
        :arg str cdda_filename: absolute CD-DA file name
        :arg str flac_filename: absolute *.flac* file name
        :arg str stdout_filename:
           absolute file name for redirected stdout
        :arg dict track_metadata: tagging fields for this track

        ``MP3Encoder`` threads are daemonized so that they are killed
        automatically if the program exits.

        """
        self.__log.call(
            track_index, cdda_filename, flac_filename, mp3_filename,
            stdout_filename, track_metadata)

        super().__init__(daemon=True)

        self.track_index = track_index
        self.cdda_filename = cdda_filename
        self.flac_filename = flac_filename
        self.mp3_filename = mp3_filename
        self.stdout_filename = stdout_filename
        self.track_metadata = track_metadata

    def run(self):
        """Decode FLAC to WAV, then encode WAV to MP3."""
        self.__log.call()

        flac_basename = os.path.basename(self.flac_filename)
        wav_tempdir = TemporaryDirectory(prefix="fm")
        wav_basename = os.path.splitext(flac_basename)[0] + ".wav"
        wav_filename = os.path.join(wav_tempdir.name, wav_basename)

        # make sure the UI gets a status update for decoding FLAC to WAV
        status = (
            self.track_index, self.cdda_filename, self.flac_filename,
            self.stdout_filename, TRACK_DECODING_WAV)
        self.__log.info("enqueueing %r", status)
        _ENCODING_QUEUE.put((3, status))

        try:
            decode_wav(
                self.flac_filename, wav_filename,
                stdout_filename=self.stdout_filename)
        except Exception as e:
            self.__log.exception("WAV decoding failed")
            del wav_tempdir
            status = (
                self.track_index, self.cdda_filename, self.flac_filename,
                self.stdout_filename, e)
            self.__log.error("enqueueing %r", status)
            _ENCODING_QUEUE.put((2, status))
            return

        # make sure the UI gets a status update for encoding WAV to MP3
        status = (
            self.track_index, self.cdda_filename, self.flac_filename,
            self.stdout_filename, TRACK_ENCODING_MP3)
        self.__log.info("enqueueing %r", status)
        _ENCODING_QUEUE.put((5, status))

        try:
            self._encode_mp3(wav_filename)
        except Exception as e:
            self.__log.exception("MP3 encoding failed")
            status = (
                self.track_index, self.cdda_filename, self.flac_filename,
                self.stdout_filename, e)
            self.__log.error("enqueueing %r", status)
            _ENCODING_QUEUE.put((2, status))
        else:
            status = (
                self.track_index, self.cdda_filename, self.flac_filename,
                self.stdout_filename, TRACK_COMPLETE)
            self.__log.info("enqueueing %r", status)
            _ENCODING_QUEUE.put((11, status))
        finally:
            del wav_tempdir

    def _encode_mp3(self, wav_filename):
        """Encode *wav_filename* to MP3 format.

        :arg str wav_filename:
           absolute path to a (temporary) WAV file

        If clipping is detected in the encoded MP3 file, *wav_filename*
        will be **re-encoded** with scaled PCM data until there is no
        clipping detected.

        """
        encode_mp3(
            wav_filename, self.mp3_filename, self.track_metadata,
            stdout_filename=self.stdout_filename)

        # check for clipping
        stdout = self.__read_stdout()
        if "WARNING: clipping occurs at the current gain." in stdout:
            clipping_occurs = True
            m = re.search(
                r"encode\s+again\s+using\s+\-\-scale\s+(\d+\.\d+)", stdout)
            scale = float(m.group(1)) if m else 0.99

            # re-encode, scaling the PCM data, until there is no clipping
            while clipping_occurs:
                self.__log.info(
                    "detected clipping in %s; re-encoding at %.2f scale...",
                    self.mp3_filename, scale)
                status = (
                    self.track_index, self.cdda_filename, self.flac_filename,
                    self.stdout_filename, TRACK_REENCODING_MP3(scale))
                _ENCODING_QUEUE.put((5, status))

                encode_mp3(
                    wav_filename, self.mp3_filename, self.track_metadata,
                    scale=scale, stdout_filename=self.stdout_filename)

                clipping_occurs = (
                    "WARNING: clipping occurs at the current gain."
                    in self.__read_stdout())
                scale -= 0.01

    def __read_stdout(self):
        """Read the MP3 encoder's *stdout*."""
        with open(self.stdout_filename) as f:
            return f.read()


class MetadataError(FLACManagerError):
    """The type of exception raised when metadata operations fail."""


@logged
class MetadataCollector:
    """Base class for collecting album and track metadata."""

    def __init__(self, toc):
        """
        :arg flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)
        self.toc = toc

    def reset(self):
        """Initialize all collection fields to default (empty)."""
        self.__log.call()

        number_of_tracks = len(self.toc.track_offsets)

        metadata = {
            "album_title": [],
            "album_discnumber": 1,
            "album_disctotal": 1,
            "album_compilation": False,
            "album_artist": [],
            "album_label": [],
            "album_genre": [],
            "album_year": [],
            "album_cover": [],
            "album_tracktotal": number_of_tracks,
            "__tracks": [None],  # 1-based indexing
            "__custom": OrderedDict(),
        }

        for i in range(number_of_tracks):
            metadata["__tracks"].append({
                "track_number": i + 1,
                "track_include": True,
                "track_title": [],
                "track_artist": [],
                "track_genre": [],
                "track_year": [],
                "__custom": OrderedDict(),
            })

        self.metadata = metadata

    def collect(self):
        """Fetch metadata from a service."""
        self.reset()


@logged
class _HTTPMetadataCollector(MetadataCollector):
    """Base class for HTTP/S metadata API clients."""

    def __init__(self, toc, api_host, use_ssl=True):
        """
        :arg flacmanager.TOC toc: a disc's table of contents
        :arg str api_host: the API host name
        :keyword bool use_ssl: whether or not to use an SSL connection

        """
        self.__log.call(toc, api_host, use_ssl=use_ssl)
        super().__init__(toc)

        # provide reasonable defaults (also see the `_ssl_context' property)
        self.user_agent = FLACManager.USER_AGENT
        self.timeout = get_config().getfloat("HTTP", "timeout")

        self.api_host = api_host
        self.use_ssl = use_ssl
        self._prepare_connection()

    def _prepare_connection(self):
        """Initialize an HTTP(S) connection to a metadata API host."""
        self.__log.call()

        if self.use_ssl:
            self._api_conx = HTTPSConnection(
                self.api_host, context=self._ssl_context, timeout=self.timeout)
        else:
            self._api_conx = HTTPConnection(
                self.api_host, timeout=self.timeout)

    @property
    @lru_cache(maxsize=1)
    def _ssl_context(self):
        """The SSL context for communicating securely with a metadata
        API host.

        :rtype: :class:`ssl.SSLContext`

        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
        context.verify_mode = ssl.CERT_NONE
        context.set_default_verify_paths()

        return context

    def _api_request(self, path, body=None, additional_headers=None):
        """Make an HTTP GET or POST API request.

        :arg str path: the request path and optional query string
        :keyword body: the HTTP request body
        :type body: :obj:`str` or :obj:`bytes`
        :keyword additional_headers:
           additional request headers (User-Agent is default)
        :type additional_headers:
           :obj:`dict` or :class:`http.client.HTTPMessage`
        :return:
           a 2-tuple containing the **closed**
           :class:`http.client.HTTPResponse` object and the response
           :obj:`bytes` (body)

        If *body* is not ``None``, the request will be an HTTP POST;
        otherwise the request will be an HTTP GET.

        This method handles redirects (301, 302, 303, 307 and 308)
        automatically.

        """
        self.__log.call(path, body=body, additional_headers=additional_headers)

        conx = self._api_conx

        headers = HTTPMessage()
        if additional_headers is not None:
            for (name, value) in additional_headers.items():
                headers[name] = value
        if "User-Agent" not in headers:
            headers["User-Agent"] = self.user_agent

        method = "POST" if body is not None else "GET"
        conx.request(method, path, body=body, headers=headers)
        response = conx.getresponse()
        while response.status in [301, 302, 303, 307, 308]:
            response.close()
            self.__log.info(
                "%s %s %s is being %d-redirected to %s",
                conx.host, method, path,
                response.status, response.msg["Location"])

            url = urlparse(response.msg["Location"])
            if (url.netloc != conx.host
                    or url.scheme !=
                        ("https" if type(conx) is HTTPSConnection else "http")
                    or response.msg["Connection"] == "close"):
                conx.close()

                if url.scheme == "https":
                    conx = HTTPSConnection(
                        url.netloc, context=self._ssl_context,
                        timeout=self.timeout)
                else:
                    conx = HTTPConnection(url.netloc, timeout=self.timeout)

            path = \
                url.path if not url.query else "%s?%s" % (url.path, url.query)

            if response.status == 303:
                method = "GET"
                body = None

            conx.request(method, path, body=body, headers=headers)
            response = conx.getresponse()

        data = response.read()
        response.close()

        if "close" in [headers["Connection"], response.msg["Connection"]]:
            conx.close()

        if conx is not self._api_conx:
            conx.close()
            self._prepare_connection()

        rv = (response, data)
        self.__log.return_(rv)
        return rv

    def _download_album_art_image(self, url):
        """GET an album art image over HTTP/S.

        :arg str url: the full URL for an album art image
        :return: the raw image :obj:`bytes` data

        """
        self.__log.call(url)

        request = Request(url, headers={"User-Agent": self.user_agent})
        response = urlopen(
            request, timeout=self.timeout,
            context=self._ssl_context if url.startswith("https:") else None)
        data = response.read()
        response.close()

        if response.status != 200:
            if response.geturl() != url:
                url = "%s (%s)" % (url, response.geturl())
            self.__log.warning(
                "GET %s -> %d %s %r",
                url, response.status, response.reason, response.getheaders())
            data = None

        self.__log.return_(data)
        return data


@logged
class GracenoteCDDBMetadataCollector(_HTTPMetadataCollector):
    """A Gracenote CDDB client that populates album and track metadata
    choices for a disc identified by its offsets.

    """

    #: Host name format string for Gracenote.
    API_HOST_TEMPLATE = "c%s.web.cddbp.net"

    #: Request path for Gracenote service calls.
    API_PATH = "/webapi/xml/1.0/"

    #: Request body for a Gracenote User ID.
    REGISTER_XML = (
        '<QUERIES>'
            '<QUERY CMD="REGISTER">'
                '<CLIENT>%s</CLIENT>'
            '</QUERY>'
        '</QUERIES>'
    )

    #: Request body for Gracenote Album summary metadata.
    ALBUM_TOC_XML = (
        '<QUERIES>'
            '<AUTH>'
                '<CLIENT>%s</CLIENT>'
                '<USER>%s</USER>'
            '</AUTH>'
            '<LANG>eng</LANG>'
            '<COUNTRY>usa</COUNTRY>'
            '<QUERY CMD="ALBUM_TOC">'
                '<TOC>'
                    '<OFFSETS>%s</OFFSETS>'
                '</TOC>'
            '</QUERY>'
        '</QUERIES>'
    )

    #: Request body for Gracenote Album and Track detail metadata.
    ALBUM_FETCH_XML = (
        '<QUERIES>'
            '<AUTH>'
                '<CLIENT>%s</CLIENT>'
                '<USER>%s</USER>'
            '</AUTH>'
            '<LANG>eng</LANG>'
            '<COUNTRY>usa</COUNTRY>'
            '<QUERY CMD="ALBUM_FETCH">'
                '<GN_ID>%s</GN_ID>'
                '<OPTION>'
                    '<PARAMETER>SELECT_EXTENDED</PARAMETER>'
                    '<VALUE>COVER</VALUE>'
                '</OPTION>'
                '<OPTION>'
                    '<PARAMETER>COVER_SIZE</PARAMETER>'
                    '<VALUE>MEDIUM,LARGE,SMALL,THUMBNAIL,XLARGE</VALUE>'
                '</OPTION>'
                '<OPTION>'
                    '<PARAMETER>SELECT_DETAIL</PARAMETER>'
                    '<VALUE>GENRE:3LEVEL</VALUE>'
                '</OPTION>'
            '</QUERY>'
        '</QUERIES>'
    )

    def __init__(self, toc):
        """
        :arg flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)

        config = get_config()
        self.__log.debug("Gracenote config = %r", dict(config["Gracenote"]))

        client_id = config.get("Gracenote", "client_id")
        if not client_id:
            raise MetadataError(
                    "Gracenote client_id must be defined in flacmanager.ini!",
                    context_hint="Gracenote configuration")
        api_host = self.API_HOST_TEMPLATE % client_id.split('-', 1)[0]
        super().__init__(toc, api_host)

        self._client_id = client_id
        self._user_id = config.get("Gracenote", "user_id")

    def _register(self):
        """Register this client with the Gracenote Web API."""
        self.__log.call()

        gn_queries = ET.fromstring(self.REGISTER_XML)
        gn_queries.find("QUERY/CLIENT").text = self._client_id

        gn_responses = self._get_response(gn_queries)
        user = gn_responses.find("RESPONSE/USER")
        self._user_id = user.text
        self.__log.debug("registered user_id = %r", self._user_id)

        get_config().set("Gracenote", "user_id", self._user_id)
        save_config()

    def collect(self):
        """Populate all Gracenote album metadata choices."""
        self.__log.call()
        super().collect()

        if not self._user_id:
            self._register()

        gn_queries = self._prepare_gn_queries(self.ALBUM_TOC_XML)
        toc_offsets = "%s %d" % (
            ' '.join(str(offset) for offset in self.toc.track_offsets),
            self.toc.leadout_track_offset)
        gn_queries.find("QUERY/TOC/OFFSETS").text = toc_offsets

        try:
            gn_responses = self._get_response(gn_queries)
        except MetadataError as e:
            if str(e) == "NO_MATCH":
                self.__log.warning("album not recognized by Gracenote")
                return
            raise

        last_album_ord = int(
            gn_responses.find("RESPONSE/ALBUM[last()]").get("ORD", 1))
        # when this equals last_album_ord, we'll send "Connection: close" in
        # the HTTP headers
        album_ord = 1
        for gn_album_summary in gn_responses.findall("RESPONSE/ALBUM"):
            gn_id = gn_album_summary.find("GN_ID").text
            gn_album_detail = self._fetch_album(
                gn_id, album_ord == last_album_ord)

            metadata = self.metadata

            num_tracks = int(gn_album_detail.find("TRACK_COUNT").text)
            if num_tracks != metadata["album_tracktotal"]:
                self.__log.warning(
                    "discarding %r; expected %d tracks but found %d",
                    gn_id, metadata["album_tracktotal"], num_tracks)
                continue

            title = gn_album_detail.find("TITLE").text
            if title not in metadata["album_title"]:
                metadata["album_title"].append(title)

            artist = gn_album_detail.find("ARTIST").text
            if artist not in metadata["album_artist"]:
                metadata["album_artist"].append(artist)

            gn_date = gn_album_detail.find("DATE")
            if (gn_date is not None
                    and gn_date.text not in metadata["album_year"]):
                metadata["album_year"].append(gn_date.text)

            for gn_genre in gn_album_detail.findall("GENRE"):
                genre = gn_genre.text
                if genre not in metadata["album_genre"]:
                    metadata["album_genre"].append(genre)

            for gn_url_coverart in gn_album_detail.findall(
                    "URL[@TYPE='COVERART']"):
                cover_art = self._download_album_art_image(
                    gn_url_coverart.text)
                if cover_art and cover_art not in metadata["album_cover"]:
                    metadata["album_cover"].append(cover_art)

            for gn_track in gn_album_detail.findall("TRACK"):
                track_number = int(gn_track.find("TRACK_NUM").text)

                track_metadata = metadata["__tracks"][track_number]

                # sanity check:
                # there are cases where the ordinality of hidden tracks
                # preceded or interspersed by empty tracks are misnumbered as
                # though the empty tracks did not exist
                # (e.g. on the single-disc re-release of Nine Inch Nails' 1992
                # EP "Broken," the hidden tracks "Physical" and "Suck" SHOULD
                # be numbered 98/99, respectively, NOT 7/8 or 8/9!!!)
                assert track_metadata["track_number"] == track_number

                title = gn_track.find("TITLE").text
                if title not in track_metadata["track_title"]:
                    track_metadata["track_title"].append(title)

                gn_artist = gn_track.find("ARTIST")
                if (gn_artist is not None
                        and gn_artist.text not in
                            track_metadata["track_artist"]):
                    track_metadata["track_artist"].append(gn_artist.text)

                for gn_genre in gn_track.findall("GENRE"):
                    genre = gn_genre.text
                    if genre not in track_metadata["track_genre"]:
                        track_metadata["track_genre"].append(genre)

            album_ord += 1

    def _fetch_album(self, gn_id, is_last_album=True):
        """Make a Gracenote 'ALBUM_FETCH' request.

        :arg str gn_id: the Gracenote ID of an album
        :keyword bool is_last_album:
           whether or not this is the last album to fetch
        :return: a Gracenote <ALBUM>
        :rtype: :class:`xml.etree.ElementTree.Element`

        """
        self.__log.call(gn_id, is_last_album=is_last_album)

        gn_queries = self._prepare_gn_queries(self.ALBUM_FETCH_XML)
        gn_queries.find("QUERY/GN_ID").text = gn_id

        gn_responses = self._get_response(
            gn_queries, http_keep_alive=is_last_album)
        gn_album = gn_responses.find("RESPONSE/ALBUM")

        self.__log.return_(gn_album)
        return gn_album

    def _prepare_gn_queries(self, xml):
        """Create a request object with authentication.

        :arg str xml:
           an XML template string for a Gracenote <QUERIES> document
        :return: the prepared Gracenote <QUERIES> document
        :rtype: :class:`xml.etree.ElementTree.Element`

        """
        self.__log.call(xml)

        gn_queries = ET.fromstring(xml)
        gn_queries.find("AUTH/CLIENT").text = self._client_id
        gn_queries.find("AUTH/USER").text = self._user_id

        self.__log.return_(gn_queries)
        return gn_queries

    def _get_response(self, gn_queries, http_keep_alive=True):
        """POST a Gracenote request and return the response.

        :arg xml.etree.ElementTree.Element gn_queries:
           a Gracenote <QUERIES> document
        :keyword bool http_keep_alive:
           whether or not to keep the Gracenote HTTP connection alive
        :return: a Gracenote <RESPONSES> document
        :rtype: :class:`xml.etree.ElementTree.Element`
        :raises MetadataError:
           if the Gracenote request is unsuccessful

        """
        self.__log.call(gn_queries, http_keep_alive=http_keep_alive)

        buf = BytesIO()
        ET.ElementTree(gn_queries).write(
            buf, encoding="UTF-8", xml_declaration=False)
        gn_queries_bytes = buf.getvalue()
        buf.close()
        self.__log.debug("gn_queries_bytes = %r", gn_queries_bytes)

        headers = {"Content-Type": "text/xml; charset=UTF-8"}
        if not http_keep_alive:
            headers["Connection"] = "close"
        (response, response_body) = self._api_request(
            self.API_PATH, body=gn_queries_bytes, additional_headers=headers)

        if response.status != 200:
            cmd = gn_queries.find("QUERY").get("CMD")
            raise MetadataError(
                "HTTP %d %s" % (response.status, response.reason),
                context_hint="Gracenote %s" % cmd)

        gn_responses = ET.fromstring(response_body.decode("UTF-8"))
        status = gn_responses.find("RESPONSE").get("STATUS")
        if status != "OK":
            cmd = gn_queries.find("QUERY").get("CMD")
            gn_message = gn_responses.find("MESSAGE")
            if gn_message is not None:
                message = "%s: %s" % (status, gn_message.text)
            else:
                message = status
            raise MetadataError(message, context_hint="Gracenote %s" % cmd)

        self.__log.return_(gn_responses)
        return gn_responses


@logged
class MusicBrainzMetadataCollector(_HTTPMetadataCollector):
    """A MusicBrainz client that populates album and track metadata
    choices for a disc identified by its MusicBrainz ID.

    """

    #: Host name for all MusicBrainz service calls.
    API_HOST = "musicbrainz.org"

    #: Request path prefix for all MusicBrainz service calls.
    API_PATH_PREFIX = "/ws/2"

    #: URL format string for cover image requests.
    COVERART_URL_TEMPLATE = "http://coverartarchive.org/release/%s/front-500"

    #: HTTP User-Agent format string for MusicBrainz requests.
    USER_AGENT_TEMPLATE = "%s ( %%s )" % FLACManager.USER_AGENT

    #: XML namespace mapping for parsing MusicBrainz responses.
    NAMESPACES = {
        "mb": "http://musicbrainz.org/ns/mmd-2.0#",
    }

    #: A reference to the ``libdiscid`` shared library.
    _LIBDISCID = None

    @classmethod
    def initialize_libdiscid(cls):
        """Load the ``libdiscid`` shared library."""
        cls.__log.call()

        config = get_config()
        try:
            libdiscid_location = resolve_path(
                config.get("MusicBrainz", "libdiscid_location"))
        except Exception as e:
            raise MetadataError(
                "MusicBrainz libdiscid_location is not valid in "
                    "flacmanager.ini",
                context_hint="MusicBrainz configuration")

        try:
            # http://jonnyjd.github.com/libdiscid/discid_8h.html
            libdiscid = C.CDLL(libdiscid_location)

            # Return a handle for a new DiscId object.
            libdiscid.discid_new.argtypes = ()
            libdiscid.discid_new.restype = C.c_void_p

            # Provides the TOC of a known CD.
            libdiscid.discid_put.argtypes = (
                C.c_void_p, C.c_int, C.c_int, C.c_void_p)
            libdiscid.discid_put.restype = C.c_int

            # Return a MusicBrainz DiscID.
            libdiscid.discid_get_id.argtypes = (C.c_void_p,)
            libdiscid.discid_get_id.restype = C.c_char_p

            # Release the memory allocated for the DiscId object.
            libdiscid.discid_free.argtypes = (C.c_void_p,)
            libdiscid.discid_free.restype = None

            cls._LIBDISCID = libdiscid
        except Exception as e:
            raise MetadataError(
                str(e), context_hint="libdiscid initialization")

    @classmethod
    def calculate_disc_id(cls, toc):
        """Return the MusicBrainz Disc ID for the disc *toc*.

        :arg flacmanager.TOC toc: a disc's table of contents
        :return: a MusicBrainz Disc ID for *toc*
        :rtype: :obj:`str`

        """
        cls.__log.call(toc)

        if cls._LIBDISCID is None:
            cls.initialize_libdiscid()

        handle = None
        try:
            handle = C.c_void_p(cls._LIBDISCID.discid_new())
            if handle is None:
                raise MetadataError(
                    "Failed to create a new libdiscid DiscId handle!",
                    context_hint="MusicBrainz libdiscid")

            offsets = [toc.leadout_track_offset] + list(toc.track_offsets)
            c_int_array = C.c_int * len(offsets)
            c_offsets = c_int_array(*offsets)

            res = cls._LIBDISCID.discid_put(
                handle,
                toc.first_track_number, toc.last_track_number, c_offsets)
            if res != 1:
                cls.__log.error(
                    "%d return from libdiscid.discid_put(handle, %d, %d, %r)",
                    res, toc.first_track_number, toc.last_track_number,
                    offsets)
                raise MetadataError(
                    "libdiscid.discid_put returned %d (expected 1)" % res,
                    context_hint="MusicBrainz libdiscid")

            disc_id = cls._LIBDISCID.discid_get_id(handle).decode("us-ascii")

            cls.__log.return_(disc_id)
            return disc_id
        finally:
            if handle is not None:
                cls._LIBDISCID.discid_free(handle)
                handle = None
                del handle

    def __init__(self, toc):
        """
        :arg flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)
        super().__init__(toc, self.API_HOST)

        config = get_config()
        self.__log.debug(
            "MusicBrainz config = %r", dict(config["MusicBrainz"]))

        contact_url_or_email = config.get(
            "MusicBrainz", "contact_url_or_email")
        if not contact_url_or_email:
            raise MetadataError(
                "MusicBrainz contact_url_or_email must be defined in "
                    "flacmanager.ini!",
                context_hint="MusicBrainz configuration")
        self.user_agent = self.USER_AGENT_TEMPLATE % contact_url_or_email

    def collect(self):
        """Populate all MusicBrainz album metadata choices."""
        self.__log.call()
        super().collect()

        nsmap = self.NAMESPACES.copy()
        self.__log.debug("using namespace map %r", nsmap)

        disc_id = self.calculate_disc_id(self.toc)
        discid_request_path = self._prepare_discid_request(disc_id)
        mb_metadata = self._get_response(
            discid_request_path, nsmap, http_keep_alive=False)

        # If there was an exact disc ID match, then the root element is <disc>.
        # Otherwise, if there was a "fuzzy" TOC match, then the root element is
        # <release-list>.
        mb_release_list = mb_metadata.find(
            "mb:disc/mb:release-list", namespaces=nsmap)
        if mb_release_list is None:
            mb_release_list = mb_metadata.find(
                "mb:release-list", namespaces=nsmap)
            if mb_release_list is None:
                raise MetadataError(
                    "No release list for disc ID %s" % disc_id,
                    context_hint="MusicBrainz API")
            else:
                self.__log.warning("fuzzy TOC match for disc_id %r", disc_id)
        else:
            self.__log.info("exact match for disc_id %r", disc_id)

        metadata = self.metadata
        for mb_release in mb_release_list.findall(
                "mb:release", namespaces=nsmap):
            # ElementTree does not use QNames for attributes in the default
            # namespace. This is fortunate, albeit incorrect, because there's
            # no way to pass the namespaces map to get(), and subbing in the
            # default namespace URI for every attribute get would be a PITA.
            release_mbid = mb_release.get("id")
            self.__log.info("processing release %r", release_mbid)

            title = mb_release.find("mb:title", namespaces=nsmap).text
            if title not in metadata["album_title"]:
                metadata["album_title"].append(title)

            mb_name = mb_release.find(
                "mb:artist-credit/mb:name-credit/mb:artist/mb:name",
                 namespaces=nsmap)
            if (mb_name is not None
                    and mb_name.text not in metadata["album_artist"]):
                album_artist = mb_name.text
                metadata["album_artist"].append(album_artist)
            else:
                album_artist = None

            mb_date = mb_release.find("mb:date", namespaces=nsmap)
            if mb_date is not None:
                year = mb_date.text.split('-', 1)[0]
                if len(year) == 4 and year not in metadata["album_year"]:
                    metadata["album_year"].append(year)

            # NOTE: MusicBrainz does not support genre information.

            barcode_node = mb_release.find("mb:barcode", namespaces=nsmap)
            barcode = barcode_node.text if barcode_node is not None else None
            if barcode:
                k = ("BARCODE", "")
                metadata["__custom"].setdefault(k, [])
                if barcode not in metadata["__custom"][k]:
                    metadata["__custom"][k].append(barcode)

            cover_art_front = mb_release.find(
                "mb:cover-art-archive/mb:front", namespaces=nsmap).text
            if cover_art_front == "true":
                cover_art = self._download_album_art_image(
                    self.COVERART_URL_TEMPLATE % release_mbid)
                if cover_art and cover_art not in metadata["album_cover"]:
                    metadata["album_cover"].append(cover_art)

            # For a multi-CD release (e.g. any Global Underground), MusicBrainz
            # returns both discs (and track lists) in <metadata>. So when we
            # encounter any <medium-list> with @count > 1, match the disc ID
            # in the <disc-list> explicitly, and only use the associated
            # <track-list>.
            # This only works when <metadata> is the result of an exact disc ID
            # match. If <metadata> is the result of a fuzzy TOC match, then any
            # release with medium-list/@count > 1 won't have its track list
            # processed.
            mb_medium_list = mb_release.find(
                "mb:medium-list", namespaces=nsmap)
            medium_count = int(mb_medium_list.get("count"))
            mb_track_list = None
            if medium_count == 1:
                mb_track_list = mb_medium_list.find(
                    "mb:medium/mb:track-list", namespaces=nsmap)
            else:
                metadata["album_disctotal"] = medium_count
                disc_path = \
                    "mb:medium/mb:disc-list/mb:disc[@id='%s']" % disc_id
                mb_disc = mb_medium_list.find(disc_path, namespaces=nsmap)
                if mb_disc is not None:
                    mb_position = mb_medium_list.find(
                        disc_path + "/../../mb:position", namespaces=nsmap)
                    metadata["album_discnumber"] = int(mb_position.text)
                    mb_track_list = mb_medium_list.find(
                            disc_path + "/../../mb:track-list",
                            namespaces=nsmap)

            if mb_track_list is None:
                self.__log.warning(
                    "unable to find a suitable track list for release %r",
                    release_mbid)
                continue

            track_count = int(mb_track_list.get("count"))
            if track_count != metadata["album_tracktotal"]:
                self.__log.warning(
                    "skipping track list (expected %d tracks, found %d)",
                    metadata["album_tracktotal"], track_count)

            for mb_track in mb_track_list.findall(
                    "mb:track", namespaces=nsmap):
                track_number = int(
                    mb_track.find("mb:number", namespaces=nsmap).text)

                track_metadata = metadata["__tracks"][track_number]

                # sanity check:
                # there are cases where the ordinality of hidden tracks
                # preceded or interspersed by empty tracks are misnumbered as
                # though the empty tracks did not exist
                # (e.g. on the single-disc re-release of Nine Inch Nails' 1992
                # EP "Broken," the hidden tracks "Physical" and "Suck" SHOULD
                # be numbered 98/99, respectively, NOT 7/8 or 8/9!!!)
                assert track_metadata["track_number"] == track_number

                title = mb_track.find(
                    "mb:recording/mb:title", namespaces=nsmap).text
                if title not in track_metadata["track_title"]:
                    track_metadata["track_title"].append(title)

                mb_name = mb_track.find(
                    "mb:recording/mb:artist-credit/mb:name-credit/mb:artist/"
                        "mb:name",
                    namespaces=nsmap)
                # MusicBrainz doesn't suppress the artist name even if it's the
                # same as the release's artist name
                if (mb_name is not None
                        and mb_name.text != album_artist
                        and mb_name.text not in
                            track_metadata["track_artist"]):
                    track_metadata["track_artist"].append(mb_name.text)

                # NOTE: MusicBrainz does not support genre information.

    def _prepare_discid_request(self, disc_id):
        """Build a full MusicBrainz '/discid' request path.

        :arg str disc_id: a MusicBrainz Disc ID
        :return: the full MusicBrainz request path
        :rtype: :obj:`str`

        """
        self.__log.call(disc_id)

        buf = StringIO()
        buf.write("%s/discid/%s" % (self.API_PATH_PREFIX, disc_id))
        buf.write(
            "?toc=%d+%d+%d" % (
                self.toc.first_track_number, len(self.toc.track_offsets),
                self.toc.leadout_track_offset))
        for track_offset in self.toc.track_offsets:
            buf.write("+%d" % track_offset)
        buf.write("&cdstubs=no&inc=artist-credits+recordings")
        request_path = buf.getvalue()
        buf.close()

        self.__log.return_(request_path)
        return request_path

    def _get_response(self, request_path, nsmap, http_keep_alive=True):
        """GET the *request_path* and return the response.

        :arg str request_path: a MusicBrainz HTTP request path
        :arg dict nsmap: namespace prefixes to URIs
        :keyword bool http_keep_alive:
           whether or not to keep the MusicBrainz HTTP connection alive
        :return: the MusicBrainz <metadata> response document
        :rtype: :class:`xml.etree.ElementTree.Element`

        If this method returns, then /metadata is guaranteed to exist;
        otherwise, a ``MetadataError`` with an appropriate message is
        rasied.

        """
        self.__log.call(request_path, nsmap, http_keep_alive=http_keep_alive)

        headers = {"User-Agent": self.user_agent}
        if not http_keep_alive:
            headers["Connection"] = "close"

        (response, response_body) = self._api_request(
            request_path, additional_headers=headers)

        if response.status != 200:
            raise MetadataError(
                "HTTP %d %s" % (response.status, response.reason),
                context_hint="MusicBrainz API")

        if "Content-Type" in response.headers:
            (_, params) = cgi.parse_header(response.headers["Content-Type"])
            encoding = params.get("charset", "ISO-8859-1")
        else:
            encoding = "ISO-8859-1"

        mb_response = ET.fromstring(response_body.decode(encoding))
        if (mb_response.tag == "error"
                or mb_response.tag != "{%s}metadata" % nsmap["mb"]):
            mb_text = mb_response.find("text")
            if mb_text is not None:
                message = mb_text.text
            else:
                message = \
                    "Unexpected response root element <%s>" % mb_response.tag
            raise MetadataError(message, context_hint="MusicBrainz API")

        self.__log.return_(mb_response)
        return mb_response


@logged
class MetadataPersistence(MetadataCollector):
    """A pseudo-client that populates **persisted** album and track
    metadata choices for a disc.

    """

    def __init__(self, toc):
        """
        :arg flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)
        super().__init__(toc)

        library_root = get_config()["Organize"]["library_root"]
        try:
            library_root = resolve_path(library_root)
        except Exception as e:
            raise MetadataError(
                "Cannot use library root %s: %s" % (library_root, e),
                context_hint="Metadata persistence",
                cause=e)

        self.metadata_persistence_root = os.path.join(
            library_root, ".metadata")
        self.disc_id = MusicBrainzMetadataCollector.calculate_disc_id(toc)
        self.metadata_filename = "%s.json" % self.disc_id
        self.metadata_path = os.path.join(
            self.metadata_persistence_root, self.metadata_filename)

    def reset(self):
        """Initialize all collection fields to default (empty)."""
        self.__log.call()
        super().reset()
        self.restored = None # handled differently as of 0.8.0
        self.converted = False

    def collect(self):
        """Populate metadata choices from persisted data."""
        self.__log.call()
        super().collect()

        if os.path.isfile(self.metadata_path):
            self.__log.debug("found %r", self.metadata_path)
            with open(self.metadata_path) as fp:
                disc_metadata = json.load(fp, object_pairs_hook=OrderedDict)

            self._postprocess(disc_metadata)

            self.metadata = disc_metadata

            self.__log.info("restored metadata %r", self.restored)
        else:
            self.__log.info("did not find %r", self.metadata_path)

    def _postprocess(self, disc_metadata):
        """Modify *metadata* in place after deserializing from JSON.

        :arg dict disc_metadata: the metadata for a disc

        .. note::
           Part of post-processing is converting the deserialized JSON
           object from a mapping of *key* -> *value* into a mapping of
           *key* -> *list-of-values*, even though each such list will
           be of length one (at most). This is to maintain consistency
           with the regular (API client) collectors, which commonly find
           multiple values for each metadata field.

        """
        if "__persisted" in disc_metadata:
            self.restored = disc_metadata.pop("__persisted")
        else:
            self.restored = dict([
                # not present in persisted metadata in versions <= 0.7.2
                ("__version__", disc_metadata.pop("__version__", None)),
                ("timestamp", disc_metadata.pop("timestamp")),
                ("TOC", disc_metadata.pop("TOC")),
                # not present in persisted metadata in versions < 0.8.0
                ("disc_id", self.disc_id),
            ])

        # the format of the persisted metadata is different as of 0.8.0
        self.converted = self.__convert_restored_metadata(disc_metadata)

        if disc_metadata["album_cover"] is not None:
            # convert album cover to byte string (raw image data) by encoding
            # the string to "Latin-1"
            # (see the `_convert_to_json_serializable(obj)' method)
            disc_metadata["album_cover"] = \
                [disc_metadata["album_cover"].encode("Latin-1")]

        for field in [
                "album_title",
                "album_artist",
                "album_label",
                "album_genre",
                "album_year",
                ]:
            disc_metadata[field] = \
                [disc_metadata[field]] if disc_metadata[field] else []

        # sanity check
        assert (
            disc_metadata["album_tracktotal"] ==
            len(self.toc.track_offsets) ==
            len(disc_metadata["__tracks"]) - 1)

        t = 1
        for track_metadata in disc_metadata["__tracks"][t:]:
            # sanity check
            assert track_metadata["track_number"] == t

            for field in [
                    "track_title",
                    "track_artist",
                    "track_genre",
                    "track_year",
                    ]:
                track_metadata[field] = \
                    [track_metadata[field]] if track_metadata[field] else []

            t += 1

        self._xform_custom_keys(literal_eval, disc_metadata)

        for track_metadata in disc_metadata["__tracks"][1:]:
            self._xform_custom_keys(literal_eval, track_metadata)

    def __convert_restored_metadata(self, disc_metadata):
        """Update the structure and property names of *disc_metadata* to
        the current FLACManager version.

        :arg dict disc_metadata:
           metadata mapping as deserialized from JSON

        .. note::
           The *disc_metadata* mapping is modified **in place**.

        """
        self.__log.call(disc_metadata)

        converted = False

        # if data is in pre-0.8.0 structure, convert it
        tracks_metadata = disc_metadata.pop("tracks", None)
        if tracks_metadata is not None:
            self.__log.info(
                "converting pre-0.8.0 persisted metadata for %s",
                self.metadata_path)
            album_metadata = disc_metadata.pop("album")
            disc_metadata.clear()
            disc_metadata.update(album_metadata)
            del album_metadata
            disc_metadata["__tracks"] = tracks_metadata

            for (old_key, new_key) in [
                    ("title", "album_title"),
                    ("disc_number", "album_discnumber"),
                    ("disc_total", "album_disctotal"),
                    ("is_compilation", "album_compilation"),
                    ("artist", "album_artist"),
                    ("record_label", "album_label"),
                    ("genre", "album_genre"),
                    ("year", "album_year"),
                    ("cover", "album_cover"),
                    ("number_of_tracks", "album_tracktotal"),
                    ]:
                value = disc_metadata.pop(old_key, None)
                disc_metadata[new_key] = \
                    value[0] if type(value) is list else value

            t = 1
            for track_metadata in disc_metadata["__tracks"][t:]:
                # sanity check
                assert track_metadata["number"] == t

                for (old_key, new_key) in [
                        ("number", "track_number"),
                        ("include", "track_include"),
                        ("title", "track_title"),
                        ("artist", "track_artist"),
                        ("genre", "track_genre"),
                        ("year", "track_year"),
                        ]:
                    value = track_metadata.pop(old_key, None)
                    track_metadata[new_key] = \
                        value[0] if type(value) is list else value

                t += 1

            converted = True
            self.__log.info(
                "converted pre-0.8.0 persisted metadata for %s to %s structure"
                    " and format",
                self.metadata_path, __version__)

        self.__log.return_(converted)
        return converted

    def store(self, metadata):
        """Persist a disc's metadata field values.

        :arg dict metadata: the finalized metadata for a disc

        Persisting the metadata field values allows for easy error
        recovery in the event that ripping/encoding fails (i.e. the user
        will not need to re-choose and/or re-enter values).

        .. note::
           The presence of persisted metadata for a disc does *not*
           prevent metadata aggregation. Metadata is still aggregated,
           but any persisted values take precedence.

        .. warning::
           Only the **first** value (i.e. the **entered** or
           **selected** value) for each metadata field is persisted.
           This value is assumed to be the preferred/intended value for
           the field.

        """
        self.__log.call(metadata)

        if not os.path.isdir(self.metadata_persistence_root):
            # doesn't work as expected for external media
            #os.makedirs(metadata_persistence_root, exist_ok=True)
            subprocess.check_call(
                ["mkdir", "-p", self.metadata_persistence_root])
            self.__log.debug("created %s", self.metadata_persistence_root)

        ordered_metadata = OrderedDict()
        ordered_metadata["__persisted"] = OrderedDict([
            ("__version__", __version__),
            ("timestamp", datetime.datetime.now().isoformat()),
            ("TOC", self.toc),
            ("disc_id", self.disc_id),
        ])
        ordered_metadata.update(metadata)

        self._preprocess(ordered_metadata)

        with open(self.metadata_path, 'w') as fp:
            json.dump(
                ordered_metadata, fp, separators=(',', ':'),
                default=self._convert_to_json_serializable)

        self.__log.info("wrote %s", self.metadata_path)

    def _preprocess(self, disc_metadata):
        """Modify *metadata* in place before serializing as JSON.

        :arg dict disc_metadata: the metadata for a disc

        """
        # replace temporary cover filename with raw image data (bytes)
        if disc_metadata["album_cover"]:
            with open(disc_metadata["album_cover"], "rb") as fp:
                disc_metadata["album_cover"] = fp.read()

        self._xform_custom_keys(repr, disc_metadata)

        for track_metadata in disc_metadata["__tracks"][1:]:
            self._xform_custom_keys(repr, track_metadata)

    def _xform_custom_keys(self, func, metadata):
        """Convert ``key`` to ``func(key)`` for each key in
        *metadata["__custom"]*.

        :arg func: the key conversion function
        :arg dict metadata: an album or track metadata mapping

        Conversion is necessary because keys in JSON objects *must* be
        strings, but FLACManager prefers to work with 2-tuple
        ``(vorbis_comment, id3v2_tag)`` keys.

        """
        if "__custom" in metadata:
            items = list(metadata["__custom"].items())
            metadata["__custom"] = OrderedDict(
                [(func(key), value) for (key, value) in items])
            self.__log.debug(
                "transformed __custom keys using %r:\n%r",
                func, metadata["__custom"])

    def _convert_to_json_serializable(self, obj):
        """Return a JSON-serializable representation of `obj`.

        :arg obj:
           an object to be converted into a serializable JSON value
        :return: a JSON-serializable representation of *obj*
        :rtype: :obj:`str`

        """
        if type(obj) is bytes:
            # JSON does not directly support binary data, so instead use the
            # Latin-1-decoded value, which will be properly converted to use
            # Unicode escape sequences by the json library.
            # (Unicode code points 0-255 are identical to the Latin-1 values.)
            return obj.decode("Latin-1")
        else:
            raise TypeError("%r is not JSON serializable" % obj)


#: Used to pass data between a :class:`MetadataAggregator` thread and
#: the main thread.
_AGGREGATOR_QUEUE = queue.Queue(1)


@logged
class MetadataAggregator(MetadataCollector, threading.Thread):
    """The thread that aggregates metadata from multiple sources."""

    def __init__(self, toc):
        """
        :arg flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)

        threading.Thread.__init__(self, daemon=True)
        MetadataCollector.__init__(self, toc)

        self.persistence = MetadataPersistence(toc)
        self._collectors = [
            self.persistence, # should be first
            GracenoteCDDBMetadataCollector(toc),
            MusicBrainzMetadataCollector(toc),
        ]
        self.exceptions = []

    def run(self):
        """Run the :meth:`collect` method in another thread."""
        self.__log.call()

        self.collect()
        self.aggregate()

        self.__log.info("enqueueing %r", self)
        _AGGREGATOR_QUEUE.put(self)

    def collect(self):
        """Collect metadata from all music databases.

        .. note::
           Persisted metadata, if it exists, is also collected by this
           method.

        """
        self.__log.call()
        super().collect()

        for collector in self._collectors:
            try:
                collector.collect()
            except Exception as e:
                self.__log.error("metadata collection error", exc_info=e)
                self.exceptions.append(e)

    def aggregate(self):
        """Combine metadata from all music databases into a single
        mapping.

        .. note::
           If persisted metadata was collected, the persisted value for
           each metadata field will take precedence over any other
           collected values.

        """
        self.__log.call()

        for collector in self._collectors:
            self._merge_metadata(
                collector.metadata, self.metadata,
                keys=[
                    "album_title",
                    "album_artist",
                    "album_label",
                    "album_genre",
                    "album_year",
                    "album_cover",
                ])

            self._merge_metadata(
                collector.metadata["__custom"], self.metadata["__custom"])

            # not terribly useful, but not sure what else could possibly be
            # done here if there are discrepancies; best to just leave it up to
            # the user to edit these fields appropriately
            for field in ["album_discnumber", "album_disctotal"]:
                if collector.metadata[field] > self.metadata[field]:
                    self.metadata[field] = collector.metadata[field]

            t = 1
            for track_metadata in collector.metadata["__tracks"][1:]:
                self._merge_metadata(
                    track_metadata, self.metadata["__tracks"][t],
                    keys=[
                        "track_title",
                        "track_artist",
                        "track_genre",
                        "track_year",
                    ])

                self._merge_metadata(
                    track_metadata["__custom"],
                    self.metadata["__tracks"][t]["__custom"])

                t += 1

        for (key, value) in self.metadata["__custom"].items():
            for track_metadata in self.metadata["__tracks"][1:]:
                if key not in track_metadata["__custom"]:
                    track_metadata["__custom"][key] = value

        # add LAME genres to album and track metadata
        self.__add_lame_genres(self.metadata["album_genre"])
        for track_metadata in self.metadata["__tracks"][1:]:
            self.__add_lame_genres(track_metadata["track_genre"])

        # currently, neither Gracenote nor MusicBrainz provide "year" metadata
        # on a per-track basis; so if "track_year" is empty after aggregation,
        # default it to the same options as "album_year"
        t = 1
        album_year = self.metadata["album_year"]
        for track_metadata in self.metadata["__tracks"][t:]:
            if not track_metadata["track_year"]:
                track_metadata["track_year"] = list(album_year) # use a copy

        # write album cover image data to temporary files
        self.__save_album_covers()

        # persisted metadata takes precedence and provides some values not
        # collected by regular collectors
        if self.persistence.restored:
            # I trust myself more than the music databases :)
            self.metadata["album_discnumber"] = \
                self.persistence.metadata["album_discnumber"]
            self.metadata["album_disctotal"] = \
                self.persistence.metadata["album_disctotal"]

            # regular collectors do not store the "album_compilation" flag
            self.metadata["album_compilation"] = \
                self.persistence.metadata["album_compilation"]

            t = 1
            for track_metadata in self.persistence.metadata["__tracks"][t:]:
                # sanity check
                assert (
                    track_metadata["track_number"] ==
                    self.metadata["__tracks"][t]["track_number"] ==
                    t)

                # regular collectors do not store the "track_include" flag
                self.metadata["__tracks"][t]["track_include"] = \
                    track_metadata["track_include"]

                t += 1

    def _merge_metadata(self, source, target, keys=None):
        """Merge *source[field]* values into *target[field]*.

        :arg dict source: metadata being merged from
        :arg dict target: metadata being merged into
        :keyword list keys:
           specific keys to merge (if not specified, **all** keys from
           *source* are merged into *target*)

        """
        self.__log.call(source, target, keys=keys)

        if keys is None:
            keys = list(source.keys())

        for key in keys:
            value = source[key]

            if key not in target:
                target[key] = value
            elif type(value) is list:
                for item in value:
                    if item not in target[key]:
                        target[key].append(item)
            elif (value is not None
                    and value not in target[key]):
                target[key].append(value)

    def __add_lame_genres(self, genres):
        """Add "official" LAME genres to *genres*.

        :arg list genres:
           the list of aggregated genres for an album or track

        *genres* is modified **in place**.

        """
        for genre in get_lame_genres():
            if genre not in genres:
                genres.append(genre)

    def __save_album_covers(self):
        """Write the binary image data for each collected album cover
        image to a temporary file.

        .. note::
           This method will **replace** the binary image data with the
           temporary file name in the aggregated metadata mapping.

        """
        self.__log.call()

        album_covers = self.metadata["album_cover"].copy()
        self.metadata["album_cover"] = []
        for (i, image_data) in enumerate(album_covers):
            image_type = imghdr.what("_ignored_", h=image_data)
            if image_type is None:
                self.__log.error(
                    "ignoring unrecognized image data [%d]: %r...",
                    i, image_data[:32])
                continue

            filepath = make_tempfile(suffix='.' + image_type)
            with open(filepath, "wb") as f:
                f.write(image_data)
            self.__log.debug("wrote %s", filepath)

            self.metadata["album_cover"].append(filepath)


@lru_cache(maxsize=1)
def get_lame_genres():
    """Return the list of genres recognized by LAME."""
    _log.call()

    genres = []
    # why does lame write the genre list to stderr!? That's lame (LOL)
    output = subprocess.check_output(
        ["lame", "--genre-list"], stderr=subprocess.STDOUT)
    for genre in StringIO(output.decode(sys.getfilesystemencoding())):
        (genre_number, genre_label) = genre.strip().split(None, 1)
        genres.append(genre_label)

    genres = tuple(genres)
    _log.return_(genres)
    return genres


def show_exception_dialog(e, aborting=False):
    """Open a dialog to display exception information.

    :arg Exception e: a caught exception
    :keyword bool aborting: ``True`` if the application will terminate

    """
    title = e.__class__.__name__
    message = str(e)
    if isinstance(e, FLACManagerError):
        if e.context_hint is not None:
            title = "%s error" % e.context_hint

        if e.cause is not None:
            message = "%s\n\n(caused by %s: %s)" % (
                message, e.cause.__class__.__name__, e)

    if aborting:
        message += (
            "\n\nWARNING! FLACManager will abort after this message is "
            "dismissed!")

    messagebox.showerror(title=title, message=message.strip())


def initialize_logging():
    """Configure the :mod:`logging` and :mod:`http.client` modules.

    This function uses options from the *flacmanager.ini* ``[Logging]``
    and ``[HTTP]`` sections to control logging output.

    """
    config = get_config()

    _log.info("Logging config = %r", dict(config["Logging"]))
    logging.basicConfig(**config["Logging"])

    _log.info("HTTP config = %r", dict(config["HTTP"]))
    HTTPConnection.debuglevel = config["HTTP"].getint("debuglevel")


if __name__ == "__main__":
    if not os.path.isfile("flacmanager.py"):
        print(
            "Please run flacmanager.py from within its directory.",
            file=sys.stderr)
        sys.exit(1)

    initialize_logging()

    ui = get_config()["UI"]
    _PADX = ui.getint("padx", _PADX)
    _PADY = ui.getint("pady", _PADY)

    try:
        FLACManager().mainloop()
    except Exception as e:
        _log.exception("aborting")
        show_exception_dialog(e, aborting=True)
        print("%s: %s" % (e.__class__.__name__, e), file=sys.stderr)
        sys.exit(1)

    sys.exit(0)

