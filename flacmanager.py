#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

# FLAC Manager -- audio metadata aggregator and FLAC+MP3 encoder
# http://ninthtest.net/flac-mp3-audio-manager/
#
# Copyright (c) 2013-2016 Matthew Zipay.
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.


__author__ = "Matthew Zipay <mattz@ninthtest.net>"
__version__ = "0.8.0-beta+dev"

"""
Please read the following articles before using FLAC Manager!

http://mzipay.github.io/FLACManager/prerequisites.html
http://mzipay.github.io/FLACManager/whats-new.html
http://mzipay.github.io/FLACManager/usage.html

"""

from ast import literal_eval
import atexit
import cgi
from collections import namedtuple, OrderedDict
from configparser import ConfigParser, ExtendedInterpolation
import ctypes as C
import datetime
from functools import partial, total_ordering
from http.client import HTTPConnection, HTTPSConnection
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
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
import tkinter.scrolledtext as scrolledtext
import tkinter.simpledialog as simpledialog
import uuid
from urllib.parse import urlparse
from urllib.request import urlopen
import xml.etree.ElementTree as ET

__all__ = [
    "QUEUE_GET_NOWAIT_AFTER",
    "get_disc_info",
    "DiscCheck",
    "TOC",
    "read_disc_toc",
    "get_config",
    "save_config",
    "make_tempfile",
    "FLACManagerError",
    "FLACManager",
    "TrackState",
    "TRACK_EXCLUDED",
    "TRACK_PENDING",
    "TRACK_ENCODING_FLAC",
    "TRACK_DECODING_WAV",
    "TRACK_ENCODING_MP3",
    "TRACK_FAILED",
    "TRACK_COMPLETE",
    "TrackEncodingStatus",
    "generate_flac_dirname",
    "generate_flac_basename",
    "generate_mp3_dirname",
    "generate_mp3_basename",
    "PrerequisitesDialog",
    "AboutDialog",
    "EditConfigurationDialog",
    "resolve_path",
    "encode_flac",
    "decode_wav",
    "encode_mp3",
    "make_vorbis_comments",
    "make_id3v2_tags",
    "FLACEncoder",
    "MP3Encoder",
    "MetadataError",
    "MetadataCollector",
    "GracenoteCDDBMetadataCollector",
    "MusicBrainzMetadataCollector",
    "MetadataPersistence",
    "MetadataAggregator",
    "get_lame_genres",
    "show_exception_dialog",
]

#: A custom tracing log level, lower in severity than :attr:`logging.DEBUG`.
TRACE = 1
logging.addLevelName(TRACE, "TRACE")


class _TracingLogger(logging.getLoggerClass()):
    """A logger with tracing capability."""

    def __init__(self, name):
        """
        :param str name: the logger name

        """
        super().__init__(name)

    def call(self, *args, **kwargs):
        """Log entry into a callable with severity :attr:`TRACE`.

        :param tuple args: the positional arguments to the callable
        :param dict kwargs: the keyword arguments to the callable

        """
        if self.isEnabledFor(TRACE):
            self._log(TRACE, "CALL *%r **%r", (args, kwargs))

    def trace(self, msg, *args, **kwargs):
        """Log 'msg % args' with severity :attr:`TRACE`.

        Positional and keyword arguments are interpreted as for
        :meth:`logging.Logger.log`.

        """
        if self.isEnabledFor(TRACE):
            self._log(TRACE, msg, *args, **kwargs)

    def mark(self, marker="MARK"):
        """Log *marker* with severity :attr:`TRACE`."""
        if self.isEnabledFor(TRACE):
            self._log(TRACE, marker)

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

    :param type cls: a class (type) object
    :return: *cls* with a provided ``__log`` member
    :rtype: :obj:`type`

    """
    setattr(cls, "_%s__log" % cls.__name__, logging.getLogger(cls.__name__))
    return cls


#: The amount of time (in milliseconds) to wait before attempting
#: another call to any :meth:`queue.Queue.get_nowait` method.
QUEUE_GET_NOWAIT_AFTER = 625


def identify_cdda_device():
    """Locate the file system device for an inserted CD-DA.

    :return: the CD-DA file system device ("/dev/*")
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

    :param str device: the CD-DA device ("/dev/*")
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

#: Used to pass data between a :class:`DiscCheck` thread and the main thread.
_DISC_QUEUE = queue.Queue(1)


@logged
class DiscCheck(threading.Thread):
    """A thread that checks for the presence of a CD-DA disc."""

    def __init__(self):
        self.__log.call()
        super().__init__(daemon=True) # kill this thread if the program exits

    def run(self):
        """Check for a disc until one is found or an exception occurs.

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

    :param str mountpoint: the mount point of an inserted CD-DA disc
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

            if (_config.read("flacmanager.ini") != ["flacmanager.ini"] or
                    "FLACManager" not in _config or
                    _config["FLACManager"].get("__version__") != __version__):
                _log.warning(
                    "flacmanager.ini is outdated; updating to version %s",
                    __version__)

                # always make sure this is accurate
                _config["FLACManager"] = OrderedDict(__version__=__version__)

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

                #TODO: add Discogs for metadata aggregation
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
                        ("album_folder", "{album_artist}/{album_title}"),
                        ("ndisc_album_folder", "${album_folder}"),
                        ("compilation_album_folder", "{album_title}"),
                        ("ndisc_compilation_album_folder",
                            "${compilation_album_folder}"),
                        ("track_filename", "{track_number:02d} {track_title}"),
                        ("ndisc_track_filename",
                            "{disc_number:02d}-${track_filename}"),
                        ("compilation_track_filename",
                            "${track_filename} ({track_artist})"),
                        ("ndisc_compilation_track_filename",
                            "{disc_number:02d}-${compilation_track_filename}"),
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
                        ("ALBUM", "{album_title}"),
                        ("ALBUMARTIST", "{album_artist}"),
                        ("ORGANIZATION", "{album_recordlabel}"),
                        ("LABEL", "${ORGANIZATION}"),
                        ("DISCNUMBER", "{disc_number:d}"),
                        ("DISCTOTAL", "{disc_total:d}"),
                        ("TRACKNUMBER", "{track_number:d}"),
                        ("TRACKTOTAL", "{track_total:d}"),
                        ("TITLE", "{track_title}"),
                        ("ARTIST", "{track_artist}"),
                        ("PERFORMER", "{track_performer}"),
                        ("GENRE", "track_genre"),
                        ("DATE", "{track_year}"),
                        ("COMPILATION", "{is_compilation:d}"),
                        ]:
                    _config["Vorbis"].setdefault(key, default_value)

                if "MP3" not in _config:
                    _config["MP3"] = OrderedDict()
                for (key, default_value) in [
                        ("library_root", "${Organize:library_root}/MP3"),
                        ("library_subroot_trie_key",
                            "${Organize:library_subroot_trie_key}"),
                        ("library_subroot_trie_level",
                            "${Organize:library_subroot_trie_level}"),
                        ("library_subroot_compilation_trie_key",
                            "${Organize:library_subroot_compilation_trie_key}"),
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
                        ("TALB", "{album_title}"),
                        ("TPE2", "{album_artist}"),
                        ("TPUB", "{album_recordlabel}"),
                        ("TPOS", "{disc_number:d}/{disc_total:d}"),
                        ("TRCK", "{track_number:d}/{track_total:d}"),
                        ("TIT2", "{track_title}"),
                        ("TIT1", "${TPE1}"),
                        ("TPE1", "{track_artist}"),
                        ("TPE4", "{track_performer}"),
                        ("TCON", "track_genre"),
                        ("TYER", "{track_year}"),
                        ("TDRC", "${TYER}"),
                        ("TCMP", "{is_compilation:d}"),
                        ]:
                    _config["ID3v2"].setdefault(key, default_value)

                with open("flacmanager.ini", 'w') as f:
                    _config.write(f)
        _log.return_(_config)
        return _config


def save_config():
    """Write the configuration settings to an INI-style file."""
    _log.call()

    with _CONFIG_LOCK, open("flacmanager.ini", 'w') as f:
        get_config().write(f)


def make_tempfile(suffix=".tmp", prefix="fm"):
    """Create a temporary file.

    :keyword str suffix: the default file extenstion
    :keyword str prefix: prepended to the beginning of the filename
    :return: the temporary file name
    :rtype: :obj:`str`

    The temporary file will be deleted automatically when the program exits.

    """
    (fd, filename) = mkstemp(suffix=suffix, prefix=prefix)
    # close the file descriptor; it isn't inherited by child processes
    os.close(fd)
    atexit.register(os.unlink, filename)
    _log.debug("created temp file %s", filename)
    return filename


class FLACManagerError(Exception):
    """The type of exception raised when FLAC Manager operations fail.

    """

    def __init__(self, message, context_hint=None, cause=None):
        """
        :param str message: error message for logging or display
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


@logged
class FLACManager(tk.Frame):
    """The FLAC Manager user interface."""

    #: The user-friendly application name.
    TITLE = "FLAC Manager"

    #: Any HTTP(S) request issued by FLAC Manager uses this value for the HTTP
    #: User-Agent header value.
    USER_AGENT = "FLACManager/%s Python/%s" % (
        __version__, sys.version.split()[0])

    def __init__(self, master=None):
        """
        :keyword master: the parent object of this frame

        """
        self.__log.call(master)
        super().__init__(master)

        self._create_menu()
        self._create_disc_status()
        self._create_editor_status()
        self.encoding_status_frame = None

        self.pack(fill=tk.BOTH, expand=tk.YES)

        if not self._missing_required_config():
            self._do_disc_check()
        else:
            self._prompt_edit_required_config()

    def _missing_required_config(self):
        """Determine whether or not required configuration settings have
        been specified.

        :return:
           ``True`` if any required configuration settings are missing,
           otherwise ``False``

        """
        self.__log.call()

        config = get_config()
        # the following options MUST be set by the user before FLACManager can
        # be used
        flag = (
            not config["Organize"].get("library_root") or
            not config["Gracenote"].get("client_id") or
            not config["MusicBrainz"].get("contact_url_or_email") or
            not config["MusicBrainz"].get("libdiscid_location")
            )

        self.__log.return_(flag)
        return flag

    def _prompt_edit_required_config(self):
        """Let the user know that required configuration settings are
        not present, and provide a button to open an editor.

        """
        self.disc_status_message.config(
            text="Required configuration is missing!", fg="Red")
        self.open_req_config_editor_button.pack(
            side=tk.RIGHT, padx=7, pady=5)

    def _edit_required_config(self):
        """Open a *flacmanager.ini* editor to allow the user to provide
        required configuration settings.

        """
        EditRequiredConfigurationDialog(
            self.master, title="Edit flacmanager.ini (required settings)")
        if not self._missing_required_config():
            self.open_req_config_editor_button.pack_forget()
            self.disc_status_message.config(
                text="Waiting for a disc to be inserted\u2026",
                fg="Black")
            self._do_disc_check()

    def _create_menu(self):
        """Create the FLAC Manager menu bar."""
        self.__log.call()

        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=tk.NO)
        file_menu.add_command(label="Exit", command=self._exit)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar)
        edit_menu.add_command(
            label="Configure metadata aggregation",
            command=self._edit_aggregation_config)
        edit_menu.add_command(
            label="Configure default folder and file names",
            command=self._edit_organization_config)
        edit_menu.add_separator()
        flac_menu = tk.Menu(edit_menu, tearoff=tk.NO)
        flac_menu.add_command(
            label="FLAC encoding options",
            command=self._edit_flac_encoding_config)
        flac_menu.add_command(
            label="FLAC Vorbis comments",
            command=self._edit_vorbis_comments_config)
        flac_menu.add_command(
            label="FLAC folder and file names",
            command=self._edit_flac_organization_config)
        edit_menu.add_cascade(label="Configure FLAC", menu=flac_menu)
        mp3_menu = tk.Menu(edit_menu, tearoff=tk.NO)
        mp3_menu.add_command(
            label="MP3 encoding options",
            command=self._edit_mp3_encoding_config)
        mp3_menu.add_command(
            label="MP3 ID3v2 tags", command=self._edit_id3v2_tags_config)
        mp3_menu.add_command(
            label="MP3 folder and file names",
            command=self._edit_mp3_organization_config)
        edit_menu.add_cascade(label="Configure MP3", menu=mp3_menu)
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Configure logging", command=self._edit_logging_config)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        help_menu = tk.Menu(menubar, tearoff=tk.NO)
        help_menu.add_command(label="Prequisites", command=self.prerequisites)
        help_menu.add_command(label="About", command=self.about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.master.config(menu=menubar)

    def _create_disc_status(self):
        """Create the disc status frame."""
        self.__log.call()

        disc_status_group = tk.LabelFrame(self, text="Disc status")
        disc_status_group.pack(fill=tk.BOTH, padx=17, pady=11)

        self.disc_eject_button = tk.Button(
            disc_status_group, text="Eject", command=self._eject_disc,
            state=tk.DISABLED)

        self.disc_status_message = tk.Label(
            disc_status_group, text="Waiting for a disc to be inserted\u2026")
        self.disc_status_message.pack(side=tk.LEFT, padx=5, pady=3)

        self.open_req_config_editor_button = tk.Button(
            disc_status_group,
            text="Edit required configuration in flacmanager.ini",
            fg="Red", activeforeground="Red",
            command=self._edit_required_config)

        self.retry_disc_check_button = tk.Button(
            disc_status_group, text="Retry disc check",
            command=self._do_disc_check)

        self.rip_and_tag_button = tk.Button(
            disc_status_group, text="Rip and tag",
            bg="Dark Green", fg="White", activebackground="Lime Green",  activeforeground="Black",
            command=self.rip_and_tag)
        _font(self.rip_and_tag_button).config(weight=tkfont.BOLD)

    def _create_editor_status(self):
        """Create the labels and buttons that communicate editor status."""
        self.__log.call()

        self.status_message_var = tk.StringVar(
            value="Aggregating metadata\u2026")
        self.status_message = tk.Label(
            self, textvariable=self.status_message_var)
        self.retry_aggregation_button = tk.Button(
            self, text="Retry metadata aggregation",
            command=self._do_metadata_aggregation)
        self.edit_offline_button = tk.Button(
            self, text="Edit metadata offline",
            command=self._edit_offline)

    def _edit_offline(self):
        """Create the metadata editor without info from a CDDB."""
        self.__log.call()

        self.retry_aggregation_button.pack_forget()
        self.edit_offline_button.pack_forget()

        try:
            self._create_metadata_editor()
        except Exception as e:
            self.__log.exception("failed to create metadata editor")
            show_exception_dialog(e)
            self.status_message.pack_forget()
            self.retry_aggregation_button.pack()
            self.edit_offline_button.pack()

    def _create_metadata_editor(self):
        """Create the metadata editor."""
        self.__log.call()

        self._current_track_number = 1
        # tracks indexing is 1-based
        self._total_tracks = len(self.toc.track_offsets)

        metadata_editor = self.metadata_editor = tk.Frame(self)
        album_editor = tk.Frame(metadata_editor)

        album_metadata = self._album_metadata

        album_title_frame = self._create_album_title_editor(
            album_editor, album_metadata["title"])
        album_title_frame.pack(fill=tk.BOTH, pady=7)

        album_artist_frame = self._create_album_artist_editor(
            album_editor, album_metadata["artist"])
        album_artist_frame.pack(fill=tk.BOTH, pady=7)

        album_performer_frame = self._create_album_performer_editor(
            album_editor, album_metadata["performer"])
        album_performer_frame.pack(fill=tk.BOTH, pady=7)

        album_recordlabel_frame = self._create_album_recordlabel_editor(
            album_editor, album_metadata["record_label"])
        album_recordlabel_frame.pack(fill=tk.BOTH, pady=7)

        album_genre_frame = self._create_album_genre_editor(
            album_editor, album_metadata["genre"])
        album_genre_frame.pack(fill=tk.BOTH, pady=7)

        year_disc_cover_row = tk.Frame(album_editor)
        album_year_frame = self._create_album_year_editor(
            year_disc_cover_row, album_metadata["year"])
        album_year_frame.pack(side=tk.LEFT)

        album_disc_frame = self._create_album_disc_editor(
            year_disc_cover_row, album_metadata["disc_number"],
            album_metadata["disc_total"])

        album_cover_frame = self._create_album_cover_editor(
            year_disc_cover_row, album_metadata["cover"])
        album_cover_frame.pack(side=tk.RIGHT)

        album_disc_frame.pack()
        year_disc_cover_row.pack(fill=tk.BOTH, pady=7)

        album_compilation_frame = self._create_album_compilation_editor(
            album_editor, album_metadata["is_compilation"])
        album_compilation_frame.pack(fill=tk.BOTH, pady=7)

        album_custom_metadata_frame = \
            self._create_album_custom_metadata_editor(album_editor)
        album_custom_metadata_frame.pack(side=tk.RIGHT)

        album_editor.pack(fill=tk.BOTH)

        tracks_editor = tk.Frame(
            metadata_editor, borderwidth=5, relief=tk.RAISED)
        self._track_vars = self._create_track_vars()
        first_track = self._tracks_metadata[1]
        track_editor = tk.Frame(tracks_editor)

        controls = tk.Frame(track_editor)

        track_nav_frame = self._create_track_navigator(
            controls, self._total_tracks)
        track_nav_frame.pack(side=tk.LEFT)

        track_include_frame = self._create_track_include_editor(controls)
        track_include_frame.pack(side=tk.LEFT, padx=29)

        custom_metadata_frame = self._create_custom_metadata_editor(controls)
        custom_metadata_frame.pack(side=tk.RIGHT)

        controls.pack(fill=tk.BOTH, pady=7)

        track_title_frame = self._create_track_title_editor(
            track_editor, first_track["title"])
        track_title_frame.pack(fill=tk.BOTH, pady=7)

        track_artist_frame = self._create_track_artist_editor(
            track_editor,
            self._combine_choices(
                first_track["artist"], album_metadata["artist"]))
        track_artist_frame.pack(fill=tk.BOTH, pady=7)

        track_performer_frame = self._create_track_performer_editor(
            track_editor,
            self._combine_choices(
                first_track["performer"], album_metadata["performer"]))
        track_performer_frame.pack(fill=tk.BOTH, pady=7)

        track_genre_frame = self._create_track_genre_editor(
            track_editor,
            self._combine_choices(
                first_track["genre"], album_metadata["genre"]))
        track_genre_frame.pack(fill=tk.BOTH, pady=7)

        track_year_frame = self._create_track_year_editor(
            track_editor,
            self._combine_choices(first_track["year"], album_metadata["year"]))
        track_year_frame.pack(side=tk.LEFT, pady=7)

        track_editor.pack(fill=tk.BOTH, padx=17, pady=17)
        tracks_editor.pack(fill=tk.BOTH, pady=29)

        self.tracks_editor = tracks_editor

        # see comments in _initialize_track_vars!
        self._initialize_track_vars()

        self.status_message.pack_forget()
        metadata_editor.pack(fill=tk.BOTH, expand=tk.YES, padx=17, pady=11)

        self.rip_and_tag_button.pack(side=tk.RIGHT, padx=7, pady=5)

        # if persisted data was restored, manually select the cover image so
        # that it opens in Preview automatically
        if self._persistence.restored and len(self._album_covers) > 1:
            # first cover is always "--none--"
            preferred_album_cover = list(self._album_covers.keys())[1]
            self.choose_cover_image(preferred_album_cover)

    def _create_track_vars(self):
        """Create metadata variables for each track."""
        self.__log.call()

        track_vars = {
            "include": [None],
            "title": [None],
            "artist": [None],
            "performer": [None],
            "genre": [None],
            "year": [None],
        }
        for track_metadata in self._tracks_metadata[1:]:
            track_vars["include"].append(
                tk.BooleanVar(value=track_metadata["include"]))
            track_vars["title"].append(tk.StringVar())
            track_vars["artist"].append(tk.StringVar())
            track_vars["performer"].append(tk.StringVar())
            track_vars["genre"].append(tk.StringVar())
            track_vars["year"].append(tk.StringVar())

        self.__log.return_(track_vars)
        return track_vars

    def _initialize_track_vars(self):
        """Set default values for all track variables."""
        self.__log.call()

        # use the tracks Spinbox as a convenient way to make sure all track
        # editors have valid values; but make sure this method is only called
        # BEFORE the metadata editor is packed, otherwise the user will be very
        # confused ;)
        while int(self.track_spinner.get()) != self._total_tracks:
            self.track_spinner.invoke("buttonup")

        # now reset the spinner back to track #1 by "wrapping around"
        self.track_spinner.invoke("buttonup")

    def _create_choices_editor(
            self, master, name, choices, width=59, var=None):
        """Create the UI to allow a value to be selected from a list of
        choices or entered directly.

        :param master: the parent object of the editor frame
        :param str name: the label for the entry and option menu
        :param list choices: a list of choices for the metadata field
        :keyword int width: the default width of the entry box
        :keyword tkinter.StringVar var:
           the variable that stores the metadata field value
        :return: the 4-tuple (label, var, entry, option_menu)
        :rtype:
           obj:`tuple` of (:class:`tkinter.Label`,
           :class:`tkinter.Variable`, :class:`tkinter.Entry`,
           :class:`tkinter.OptionMenu`)

        The optional *var* is created as a :class:`tkinter.StringVar`
        if it is not provided.

        """
        self.__log.call(master, name, choices, width=width, var=var)

        label = tk.Label(master, text=name)

        if var is None:
            var = tk.StringVar()

        if choices:
            var.set(choices[0])
        else:
            # this is just so that the OptionMenu can be created; but if this
            # happens, the optionmenu won't be packed
            choices = [""]

        entry = tk.Entry(
            master, exportselection=tk.NO, textvariable=var, width=width)
        optionmenu = tk.OptionMenu(
            master, var, *choices, command=lambda v: var.set(v))

        self.__log.return_((label, var, entry, optionmenu))
        return (label, var, entry, optionmenu)

    def _refresh_choices_editor(self, var, entry, optionmenu, choices):
        """Update the values for the given editor controls.

        :param tkinter.Variable var:
           holds the value for a metadata field
        :param tkinter.Entry entry:
           the data entry control for a metadata field
        :param tkinter.OptionMenu optionmenu:
           the drop-down option menu for a metadata field
        :param list choices:
           new choices for the metadata field
        :return: a new drop-down option menu for the metadata field
        :rtype: :class:`tkinter.OptionMenu`

        .. note::
           If *choices* is empty, the caller will not pack the
           newly-created :class:`tkinter.OptionMenu` returned by this
           method.

        """
        self.__log.call(var, entry, optionmenu, choices)

        master = optionmenu.master
        optionmenu.destroy()
        optionmenu = None

        # always prefer the current value if it's not empty
        if not var.get():
            var.set(choices[0] if choices else "")

        if not choices:
            # this is just so that the OptionMenu can be created; but if this
            # happens, the new_optionmenu won't be packed
            choices = [var.get()]

        entry.config(textvariable=var)
        new_optionmenu = tk.OptionMenu(
            master, var, *choices, command=lambda v: var.set(v))

        self.__log.return_(new_optionmenu)
        return new_optionmenu

    def _create_album_title_editor(self, master, choices):
        """Create the UI editing controls for the album title.

        :param master: parent object of the editor frame
        :param list choices: aggregated values for the album title
        :return: the album title editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, self.album_title_var, entry, optionmenu) = \
            self._create_choices_editor(frame, "Album", choices)

        label.pack(side=tk.LEFT)
        entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            optionmenu.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_album_artist_editor(self, master, choices):
        """Create the UI editing controls for the album title.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the album artist
        :return: the album artist editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, self.album_artist_var, entry, optionmenu) = \
            self._create_choices_editor(frame, "Artist", choices)
        button = tk.Button(
            frame, text="Apply to all tracks",
            command=self.apply_album_artist_to_tracks)

        label.pack(side=tk.LEFT)
        entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            optionmenu.pack(side=tk.LEFT)

        button.pack(side=tk.LEFT, padx=5)

        self.__log.return_(frame)
        return frame

    def apply_album_artist_to_tracks(self):
        """Set each track artist to the album artist."""
        self.__log.call()

        album_artist_value = self.album_artist_var.get()
        for track_artist_var in self._track_vars["artist"][1:]:
            track_artist_var.set(album_artist_value)

    def _create_album_performer_editor(self, master, choices):
        """Create the UI editing controls for the album performer.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the album performer
        :return: the album performer editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, self.album_performer_var, entry, optionmenu) = \
            self._create_choices_editor(frame, "Performer", choices)
        button = tk.Button(
            frame, text="Apply to all tracks",
            command=self.apply_album_performer_to_tracks)

        label.pack(side=tk.LEFT)
        entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            optionmenu.pack(side=tk.LEFT)

        button.pack(side=tk.LEFT, padx=5)

        self.__log.return_(frame)
        return frame

    def apply_album_performer_to_tracks(self):
        """Set each track performer to the album performer."""
        self.__log.call()

        album_performer_value = self.album_performer_var.get()
        for track_performer_var in self._track_vars["performer"][1:]:
            track_performer_var.set(album_performer_value)

    def _create_album_recordlabel_editor(self, master, choices):
        """Create the UI editing controls for the record label.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the record label
        :return: the record label editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, self.album_recordlabel_var, entry, optionmenu) = \
            self._create_choices_editor(frame, "Label", choices)

        label.pack(side=tk.LEFT)
        entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            optionmenu.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_album_genre_editor(self, master, choices):
        """Create the UI editing controls for the album genre.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the album genre
        :return: the album genre editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        genres = self._combine_genres(choices)
        (label, self.album_genre_var, entry, optionmenu) = \
            self._create_choices_editor(frame, "Genre", genres, width=29)

        self._add_lame_genres_menu(optionmenu, self.album_genre_var, genres)

        button = tk.Button(
            frame, text="Apply to all tracks",
            command=self.apply_album_genre_to_tracks)

        label.pack(side=tk.LEFT)
        entry.pack(side=tk.LEFT, padx=5)
        optionmenu.pack(side=tk.LEFT)
        button.pack(side=tk.LEFT, padx=5)

        self.__log.return_(frame)
        return frame

    def _combine_genres(self, choices):
        """Create a custom genre list from a list of aggregated genres.

        :param list choices: aggregated values for the album genre
        :return: customized :obj:`list` of values for the album genre

        If a genre choice has been restored from persisted data, it will
        always remain in place as the *first* choice.

        If *choices* is empty, add "Other" to the list.

        """
        self.__log.call(choices)

        genres = []
        for choice in choices:
            for single_genre in [genre.strip() for genre in choice.split(',')]:
                if single_genre not in genres:
                    genres.append(single_genre)

        if len(genres) > 1:
            combined = ", ".join(genres)
            genres.insert(0, combined)
        elif len(genres) == 0:
            genres.append("Other")

        if self._persistence.restored and choices and genres[0] != choices[0]:
            if choices[0] in genres:
                genres.remove(choices[0])
            genres.insert(0, choices[0])

        self.__log.return_(genres)
        return genres

    def _add_lame_genres_menu(self, optionmenu, var, excludes):
        """Add a submenu to *optionmenu* that contains the LAME genres.

        :param tkinter.OptionMenu optionmenu:
           a drop-down menu of LAME genre choices
        :param tkinter.StringVar var:
           a variable to hold the selected genre
        :param list excludes: LAME genres to exclude from the submenu

        """
        self.__log.call(optionmenu, var, excludes)

        optionmenu["menu"].add_separator()
        menu = tk.Menu(optionmenu["menu"])
        for genre in get_lame_genres():
            if genre not in excludes:
                menu.add_command(
                    label=genre, command=lambda v=var, g=genre: v.set(g))
        optionmenu["menu"].add_cascade(label="LAME", menu=menu)

    def apply_album_genre_to_tracks(self):
        """Set each track genre to the album genre."""
        self.__log.call()

        album_genre_value = self.album_genre_var.get()
        for track_genre_var in self._track_vars["genre"][1:]:
            track_genre_var.set(album_genre_value)

    def _create_album_year_editor(self, master, choices):
        """Create the UI editing controls for the album year.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the album year
        :return: the album genre editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, self.album_year_var, entry, optionmenu) = \
            self._create_choices_editor(frame, "Year", choices, width=5)
        button = tk.Button(
            frame, text="Apply to all tracks",
            command=self.apply_album_year_to_tracks)

        label.pack(side=tk.LEFT)
        entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            optionmenu.pack(side=tk.LEFT)

        button.pack(side=tk.LEFT, padx=5)

        self.__log.return_(frame)
        return frame

    def apply_album_year_to_tracks(self):
        """Set each track genre to the album genre."""
        self.__log.call()

        album_year_value = self.album_year_var.get()
        for track_year_var in self._track_vars["year"][1:]:
            track_year_var.set(album_year_value)

    def _create_album_disc_editor(self, master, number, total):
        """Create the UI editing controls for the album disc.

        :param master: parent obejct of the editor frame
        :param int number: default disc number
        :param int total: default disc total
        :return: the album disc number/total editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, number, total)

        frame = tk.Frame(master)

        number_label = tk.Label(frame, text="Disc")
        number_label.pack(side=tk.LEFT)

        self.album_disc_number_var = tk.StringVar(value=str(number))
        number_entry = tk.Entry(
            frame, exportselection=tk.NO,
            textvariable=self.album_disc_number_var, width=2)
        number_entry.pack(side=tk.LEFT, padx=2)

        total_label = tk.Label(frame, text="of")
        total_label.pack(side=tk.LEFT)

        self.album_disc_total_var = tk.StringVar(value=str(total))
        total_entry = tk.Entry(
            frame, exportselection=tk.NO,
            textvariable=self.album_disc_total_var, width=2)
        total_entry.pack(side=tk.LEFT, padx=2)

        self.__log.return_(frame)
        return frame

    def _create_album_cover_editor(self, master, choices):
        """Create the UI editing controls for the album cover.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the album cover
        :return: the album cover editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        self._album_covers = OrderedDict()
        self._album_covers["--none--"] = None
        for image_data in choices:
            self._save_cover_image(image_data)

        frame = tk.Frame(master)

        label = tk.Label(frame, text="Cover")
        label.pack(side=tk.LEFT)

        self.album_cover_var = tk.StringVar(value="--none--")
        self._covers_optionmenu = tk.OptionMenu(
            frame, self.album_cover_var, *self._album_covers.keys(),
            command=self.choose_cover_image)
        self._covers_optionmenu.pack(side=tk.LEFT, padx=5)

        url_button = tk.Button(
            frame, text="Add URL", command=self.choose_cover_image_from_url)
        url_button.pack(side=tk.LEFT, padx=5)

        file_button = tk.Button(
            frame, text="Add file", command=self.choose_cover_image_from_file)
        file_button.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_album_compilation_editor(self, master, value):
        """Create a checkbox to indicate that the album is a
        compilation.

        :param master: parent obejct of the editor frame
        :param bool value: initial value of the checkbox
        :return: the compilation checkbox editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, value)

        frame = tk.Frame(master)

        self.album_compilation_var = tk.BooleanVar(value=value)
        checkbutton = tk.Checkbutton(
            master, text="Compilation", variable=self.album_compilation_var,
            onvalue=True, offvalue=False)
        checkbutton.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_album_custom_metadata_editor(self, master):
        """Create the UI editing controls for editing custom metadata
        for *all* tracks.

        :param master: parent obejct of the editor frame
        :return: the custom metadata editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master)

        frame = tk.Frame(master)

        edit_album_custom_metadata_button = tk.Button(
            frame, text="Edit custom Vorbis/ID3v2 tagging for ALL tracks",
            command=
                lambda self=self:
                    EditAlbumCustomMetadataTaggingDialog(
                        master, self._album_metadata, self._tracks_metadata,
                        title=self.album_title_var.get()))
        edit_album_custom_metadata_button.pack()

        self.__log.return_(frame)
        return frame

    def _save_cover_image(self, image_data):
        """Save raw image bytes to a temporary file.

        :param bytes image_data: the raw image data
        :return: 2-tuple (:obj:`str` label, :obj:`str` filename)

        """
        self.__log.call(image_data)

        image_type = imghdr.what("_ignored_", h=image_data)
        if image_type is None:
            raise MetadataError(
                "Unrecognized image type.", context_hint="Save image")

        name = "Cover #%d (%s)" % (len(self._album_covers), image_type.upper())
        filename = make_tempfile(suffix='.' + image_type)
        with open(filename, "wb") as f:
            f.write(image_data)
        self._album_covers[name] = filename

        self.__log.debug("%r -> %s", name, filename)
        return (name, filename)

    def choose_cover_image(self, name):
        """Select the named cover image and open in Mac OS X Preview.

        :param str name: label for the image

        """
        self.__log.call(name)

        filename = self._album_covers.get(name)
        if filename:
            status = subprocess.call(["open", "-a", "Preview", filename])
            if status == 0:
                self.album_cover_var.set(name)
            else:
                self.__log.warning(
                    "exit status %d attempting to preview %s",
                    status, filename)
        else:
            # should never happen
            self.__log.warning("%r is not mapped to a filename", name)
            messagebox.showwarning(
                "Invalid image choice",
                "%s is not a valid image choice!" % name)

    def choose_cover_image_from_url(self):
        """Download a cover image from a user-provided URL and open in
        Mac OS X Preview.

        The downloaded cover image is made available in the cover image
        dropdown.

        """
        self.__log.call()

        self._covers_optionmenu.config(state=tk.DISABLED)

        # leave initialvalue empty (paste-over doesn't work in XQuartz)
        url = simpledialog.askstring(
            "Add a cover image from a URL", "Enter the image URL:",
            initialvalue="")
        if not url:
            self._covers_optionmenu.config(state=tk.NORMAL)
            self.__log.return_()
            return

        self.__log.debug("url = %r", url)
        name = None
        try:
            response = urlopen(
                url, timeout=get_config().getfloat("HTTP", "timeout"))
            image_data = response.read()
            response.close()

            if response.status != 200:
                raise RuntimeError(
                    "HTTP %d %s" % (response.status, response.reason))

            (name, filename) = self._save_cover_image(image_data)
        except Exception as e:
            self.__log.exception("failed to obtain image from %r", url)
            messagebox.showerror(
                "Image download failure",
                "An unexpected error occurred while "
                    "downloading the image from %s." % url)
        else:
            self._add_album_cover_option(name)
            messagebox.showinfo(
                "Cover image added",
                "%s has been added to the available covers." % name)
        finally:
            self._covers_optionmenu.config(state=tk.NORMAL)

        if name is not None:
            self.choose_cover_image(name)

    def _add_album_cover_option(self, name):
        """Add *name* to the cover image dropdown menu."""
        self.__log.call(name)

        self._covers_optionmenu["menu"].add_command(
            label=name,
            command=lambda f=self.choose_cover_image, v=name: f(v))

    def choose_cover_image_from_file(self):
        """Open a user-defined cover image in Mac OS X Preview.

        The cover image is made available in the cover image dropdown.

        """
        self.__log.call()

        self._covers_optionmenu.config(state=tk.DISABLED)

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
            self._covers_optionmenu.config(state=tk.NORMAL)
            self.__log.return_()
            return

        self.__log.debug("filename = %r", filename)
        name = None
        if os.path.isfile(filename):
            with open(filename, "rb") as f:
                image_data = f.read()

            image_type = imghdr.what("_ignored_", h=image_data)
            if image_type is None:
                messagebox.showwarning(
                    "Image add failure",
                    "Type of %s is not recognized!" % filename)
                self._covers_optionmenu.config(state=tk.NORMAL)

            name = "Cover #%d (%s)" % (
                len(self._album_covers), image_type.upper())
            self._album_covers[name] = filename
            self._add_album_cover_option(name)
            messagebox.showinfo(
                "Cover image added",
                "%s has been added to the available covers." % name)
        else:
            self.__log.error("file not found: %r", filename)
            messagebox.showerror(
                "Image add failure", "File not found: %s" % filename)

        self._covers_optionmenu.config(state=tk.NORMAL)

        if name is not None:
            self.choose_cover_image(name)

    def _create_track_navigator(self, master, total_tracks):
        """Create the UI controls for navigating between tracks.

        :param master: parent object of the navigation frame
        :param int total_tracks: number of tracks on the album
        :return: track navigation controls
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, total_tracks)

        frame = tk.Frame(master)

        track_label = tk.Label(frame, text="Track")
        track_label.pack(side=tk.LEFT)

        self.track_spinner = tk.Spinbox(
            frame, from_=1, to=total_tracks, width=3, wrap=True,
            command=self.refresh_track_editors)
        self.track_spinner.pack(side=tk.LEFT, padx=5)

        of_label = tk.Label(frame, text="of %d" % total_tracks)
        of_label.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_track_include_editor(self, master):
        """Create the UI controls for including/excluding a track.

        :param master: parent object of the editor frame
        :return: the track include editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master)

        frame = tk.Frame(master)

        self.track_include_checkbox = tk.Checkbutton(
            frame, text="Include this track",
            fg="Blue", activeforeground="Blue",
            variable=self._track_vars["include"][1],
            onvalue=True, offvalue=False,
            command=self.toggle_track_include_state)
        self.track_include_checkbox.pack(side=tk.LEFT, padx=11)

        self.toggle_all_tracks_include_button = tk.Button(
            frame, text="Include all tracks",
            command=self.apply_include_to_tracks)
        self.toggle_all_tracks_include_button.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def apply_include_to_tracks(self):
        """Set each track to be included/excluded based on the current
        track's include/exclude state.

        """
        self.__log.call()

        include_value = \
            self._track_vars["include"][self._current_track_number].get()
        for track_include_var in self._track_vars["include"][1:]:
            track_include_var.set(include_value)

    def toggle_track_include_state(self):
        """Enable/disable editors for the current track based on whether
        it is include or excluded, respectively.

        """
        self.__log.call()

        if not self._track_vars["include"][self._current_track_number].get():
            self.track_include_checkbox.config(
                fg="Red", activeforeground="Red")
            self.toggle_all_tracks_include_button.config(
                text="Exclude all tracks")
        else:
            self.track_include_checkbox.config(
                fg="Blue", activeforeground="Blue")
            self.toggle_all_tracks_include_button.config(
                text="Include all tracks")

    def _create_track_title_editor(self, master, choices):
        """Create the UI editing controls for the track title.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the track title
        :return: the track title editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, _, self.track_title_entry, self._track_title_optionmenu) = \
            self._create_choices_editor(
                frame, "Title", choices, var=self._track_vars["title"][1])

        label.pack(side=tk.LEFT)
        self.track_title_entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            self._track_title_optionmenu.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_track_artist_editor(self, master, choices):
        """Create the UI editing controls for the track artist.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the track artist
        :return: the track artist editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, _, self.track_artist_entry, self._track_artist_optionmenu) = \
            self._create_choices_editor(
                frame, "Artist", choices, var=self._track_vars["artist"][1])

        label.pack(side=tk.LEFT)
        self.track_artist_entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            self._track_artist_optionmenu.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_track_performer_editor(self, master, choices):
        """Create the UI editing controls for the track performer.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the track performer
        :return: the track performer editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, _, self.track_performer_entry,
                self._track_performer_optionmenu) = \
            self._create_choices_editor(
                frame, "Performer", choices,
                var=self._track_vars["performer"][1])

        label.pack(side=tk.LEFT)
        self.track_performer_entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            self._track_performer_optionmenu.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_track_genre_editor(self, master, choices):
        """Create the UI editing controls for the track genre.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the track genre
        :return: the track genre editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        genres = self._combine_genres(choices)
        (label, _, self.track_genre_entry, self._track_genre_optionmenu) = \
            self._create_choices_editor(
                frame, "Genre", genres, width=29,
                var=self._track_vars["genre"][1])

        self._add_lame_genres_menu(
            self._track_genre_optionmenu, self.track_genre_entry, genres)

        label.pack(side=tk.LEFT)
        self.track_genre_entry.pack(side=tk.LEFT, padx=5)
        self._track_genre_optionmenu.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_track_year_editor(self, master, choices):
        """Create the UI editing controls for the track year.

        :param master: parent obejct of the editor frame
        :param list choices: aggregated values for the track year
        :return: the track year editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, choices)

        frame = tk.Frame(master)

        (label, _, self.track_year_entry, self._track_year_optionmenu) = \
            self._create_choices_editor(
                frame, "Year", choices, width=5,
                var=self._track_vars["year"][1])

        label.pack(side=tk.LEFT)
        self.track_year_entry.pack(side=tk.LEFT, padx=5)

        if len(choices) > 1:
            self._track_year_optionmenu.pack(side=tk.LEFT)

        self.__log.return_(frame)
        return frame

    def _create_custom_metadata_editor(self, master):
        """Create the UI editing controls for editing custom metadata
        for a single track.

        :param master: parent obejct of the editor frame
        :return: the custom metadata editor
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master)

        frame = tk.Frame(master)

        self._edit_custom_metadata_button = tk.Button(
            frame, text="Edit custom Vorbis/ID3v2 tagging for this track",
            command=
                lambda self=self:
                    EditCustomMetadataTaggingDialog(
                        master,
                        self._tracks_metadata[self._current_track_number],
                        title="Track %d %s" % (
                            self._current_track_number,
                            self._track_vars["title"][
                                self._current_track_number].get())))
        self._edit_custom_metadata_button.pack()

        self.__log.return_(frame)
        return frame

    def refresh_track_editors(self):
        """Populate track editors with metadata for the current track.

        This method is called when navigating to another track. If
        navigation is at either end of the track list already (i.e.
        navigating "down" from track 1 or "up" from the last track),
        then this method is a no-op.

        """
        self.__log.call()

        track_number = int(self.track_spinner.get())
        if self._current_track_number == track_number:
            self.__log.return_()
            return

        self._current_track_number = track_number
        self.toggle_track_include_state()
        track_metadata = self._tracks_metadata[track_number]

        track_include_var = self._track_vars["include"][track_number]
        track_title_var = self._track_vars["title"][track_number]
        track_artist_var = self._track_vars["artist"][track_number]
        track_performer_var = self._track_vars["performer"][track_number]
        track_genre_var = self._track_vars["genre"][track_number]
        track_year_var = self._track_vars["year"][track_number]

        self.track_include_checkbox.config(variable=track_include_var)

        self._track_title_optionmenu = self._refresh_choices_editor(
            track_title_var, self.track_title_entry,
            self._track_title_optionmenu, track_metadata["title"])
        if len(track_metadata["title"]) > 1:
            self._track_title_optionmenu.pack(side=tk.LEFT)

        choices = self._combine_choices(
            track_metadata["artist"], self._album_metadata["artist"])
        self._track_artist_optionmenu = self._refresh_choices_editor(
            track_artist_var, self.track_artist_entry,
            self._track_artist_optionmenu, choices)
        if len(choices) > 1:
            self._track_artist_optionmenu.pack(side=tk.LEFT)

        choices = self._combine_choices(
            track_metadata["performer"], self._album_metadata["performer"])
        self._track_performer_optionmenu = self._refresh_choices_editor(
            track_performer_var, self.track_performer_entry,
            self._track_performer_optionmenu, choices)
        if len(choices) > 1:
            self._track_performer_optionmenu.pack(side=tk.LEFT)

        choices = self._combine_choices(
            track_metadata["genre"], self._album_metadata["genre"])
        genres = self._combine_genres(choices)

        self._track_genre_optionmenu = self._refresh_choices_editor(
            track_genre_var, self.track_genre_entry,
            self._track_genre_optionmenu, genres)
        self._track_genre_optionmenu["menu"].add_separator()

        menu = tk.Menu(self._track_genre_optionmenu["menu"])
        for genre in get_lame_genres():
            if genre not in genres:
                menu.add_command(
                    label=genre,
                    command=lambda v=track_genre_var, g=genre: v.set(g))
        self._track_genre_optionmenu["menu"].add_cascade(
            label="LAME", menu=menu)

        if len(choices) > 1:
            self._track_genre_optionmenu.pack(side=tk.LEFT)

        choices = self._combine_choices(
            track_metadata["year"], self._album_metadata["year"])
        self._track_year_optionmenu = self._refresh_choices_editor(
            track_year_var, self.track_year_entry,
            self._track_year_optionmenu, choices)
        if len(choices) > 1:
            self._track_year_optionmenu.pack(side=tk.LEFT)

    def _combine_choices(self, preferred, additional):
        """Combine two lists of values.

        :param list preferred: the preferred choices
        :param list additional: additional choices
        :return: the union of *preferred* and *additional*

        A new list is always returned. All items from *preferred* are
        added to the new list. Only items from *additional* that
        are **not** already in *preferred* are added to the new list.
        The order of preferred/additional choices is maintained.

        """
        self.__log.call(preferred, additional)

        combined = list(preferred)
        for choice in additional:
            if choice not in combined:
                combined.append(choice)

        self.__log.return_(combined)
        return combined

    def _persist_metadata(self):
        """Store the current metadata field/value pairs."""
        self.__log.call()

        number_of_tracks = len(self.toc.track_offsets)
        album_metadata = {
            "title": [self.album_title_var.get()]
                if self.album_title_var.get() else [],
            "artist": [self.album_artist_var.get()]
                if self.album_artist_var.get() else [],
            "performer": [self.album_performer_var.get()]
                if self.album_performer_var.get() else [],
            "record_label": [self.album_recordlabel_var.get()]
                if self.album_recordlabel_var.get() else [],
            "year": [self.album_year_var.get()]
                if self.album_year_var.get() else [],
            "genre": [self.album_genre_var.get()]
                if self.album_genre_var.get() else [],
            "cover": [self._album_covers.get(self.album_cover_var.get())]
                if self.album_cover_var.get() != "--none--" else [],
            "number_of_tracks": number_of_tracks,
            "is_compilation": self.album_compilation_var.get(),
            "disc_number": int(self.album_disc_number_var.get())
                if self.album_disc_number_var.get() else 1,
            "disc_total": int(self.album_disc_total_var.get())
                if self.album_disc_total_var.get() else 1,
        }
        if self._album_metadata.get("__custom"):
            album_metadata["__custom"] = self._album_metadata["__custom"]

        # for persistence, replace temporary cover filenames with raw image
        # data (byte strings)
        for i in range(len(album_metadata["cover"])):
            with open(album_metadata["cover"][i], "rb") as fp:
                album_metadata["cover"][i] = fp.read()

        track_vars = self._track_vars
        tracks_metadata = [None] # 1-based indexing for tracks
        for i in range(number_of_tracks):
            track_number = i + 1
            track_metadata = {
                "include": track_vars["include"][track_number].get(),
                "number": track_number,
                "title": [track_vars["title"][track_number].get()]
                    if track_vars["title"][track_number].get() else [],
                "artist": [track_vars["artist"][track_number].get()]
                    if track_vars["artist"][track_number].get() else [],
                "performer": [track_vars["performer"][track_number].get()]
                    if track_vars["performer"][track_number].get() else [],
                "year": [track_vars["year"][track_number].get()]
                    if track_vars["year"][track_number].get() else [],
                "genre": [track_vars["genre"][track_number].get()]
                    if track_vars["genre"][track_number].get() else [],
            }
            if self._tracks_metadata[track_number].get("__custom"):
                track_metadata["__custom"] = \
                    self._tracks_metadata[track_number]["__custom"]
            tracks_metadata.append(track_metadata)

        self._persistence.store({
            "album": album_metadata,
            "tracks": tracks_metadata,
        })

    def _prepare_tagging_metadata(self):
        """Build the track-centric data structure that contains the
        final metadata values to be used for tagging.

        :return:
           the :obj:`list` of metadata :obj:`dict` objects for all
           included tracks

        .. note::
           Any tracks that are "excluded" are not processed, and
           ``None`` is added to the returned list instead of a dict.

        """
        self.__log.call()

        # not set from metadata collectors, so set it now
        self._album_metadata["is_compilation"] = \
            self.album_compilation_var.get()

        # metadata per track will be initialized with a copy of this
        # mapping
        album_metadata = dict(
            album_title=self.album_title_var.get(),
            album_artist=self.album_artist_var.get(),
            album_performer=self.album_performer_var.get(),
            album_recordlabel=self.album_recordlabel_var.get(),
            album_genre=
                re.split(r"\s*,\s*", self.album_genre_var.get()),
            album_year=self.album_year_var.get(),
            disc_number=int(self.album_disc_number_var.get()),
            disc_total=int(self.album_disc_total_var.get()),
            album_cover=self._album_covers.get(self.album_cover_var.get()),
            is_compilation=self.album_compilation_var.get(),
            track_total=len(self.toc.track_offsets)
        )

        track_vars = self._track_vars
        tagging_metadata = []
        for i in range(album_metadata["track_total"]):
            track_number = i + 1
            if track_vars["include"][track_number].get():
                track_metadata = dict(album_metadata)
                track_metadata.update(dict(
                    track_number=track_number,
                    track_title=track_vars["title"][track_number].get(),
                    track_artist=track_vars["artist"][track_number].get(),
                    track_performer=
                        track_vars["performer"][track_number].get(),
                    track_genre=
                        re.split(
                            r"\s*,\s*",
                            track_vars["genre"][track_number].get()),
                    track_year=track_vars["year"][track_number].get()
                ))
                if self._tracks_metadata[track_number].get("__custom"):
                    track_metadata["__custom"] = \
                        self._tracks_metadata[track_number]["__custom"]
                tagging_metadata.append(track_metadata)
            else:
                self.__log.info("track %d is excluded", track_number)
                tagging_metadata.append(None)

        self.__log.return_(tagging_metadata)
        return tagging_metadata

    def _create_encoder_status(self, master, max_visible_tracks=29):
        """Create UI widgets that communicate encoding status to the
        user.

        :param master:
           parent object of the encoding status widgets frame
        :keyword int max_visible_tracks:
           the number of tracks displayed before scrolling
        :return: the encoding status widgets
        :rtype: :class:`tkinter.Frame`

        """
        self.__log.call(master, max_visible_tracks=max_visible_tracks)

        encoding_status_frame = tk.LabelFrame(master, text="Encoding status")

        track_titles = [var.get() for var in self._track_vars["title"][1:]]
        self.track_labels = [
            "%02d %s" % (i + 1, v) for (i, v) in enumerate(track_titles)]

        track_include_flags = [
            var.get() for var in self._track_vars["include"][1:]]
        self._initialize_track_encoding_statuses(track_include_flags)

        track_total = len(self.toc.track_offsets)

        list_frame = tk.Frame(encoding_status_frame)
        if track_total > max_visible_tracks:
            visible_tracks = max_visible_tracks
            vscrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
            vscrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            cfg = {"yscrollcommand": vscrollbar.set}
        else:
            visible_tracks = track_total
            vscrollbar = None
            cfg = {}

        self._encoding_status_list = tk.Listbox(
            list_frame, exportselection=tk.NO, activestyle=tk.NONE,
            selectmode=tk.SINGLE, bd=1, height=visible_tracks,
            listvariable=tk.StringVar(
                value=' '.join(
                    "{%s}" % track_encoding_status.describe()
                    for track_encoding_status in
                        self._track_encoding_statuses)),
            **cfg)

        if vscrollbar is not None:
            vscrollbar.config(command=self._encoding_status_list.yview)

        list_frame.pack(fill=tk.BOTH, padx=17, pady=17)

        for i in range(track_total):
            if not track_include_flags[i]:
                self._encoding_status_list.itemconfig(i, {"fg": "gray79"})

        self._encoding_status_list.pack(fill=tk.BOTH, padx=0, pady=0)

        self.__log.return_(encoding_status_frame)
        return encoding_status_frame

    def _initialize_track_encoding_statuses(self, track_include_flags):
        """Create the state machines for each track's encoding status.

        :param list track_include_flags:
           ``True`` or ``False`` values for each track indicating
           whether or not it is included for the encoding process

        """
        self.__log.call(track_include_flags)

        self._track_encoding_statuses = []
        for (i, label) in enumerate(self.track_labels):
            self._track_encoding_statuses.append(
                TrackEncodingStatus(label, pending=track_include_flags[i]))

    def rip_and_tag(self):
        """Create tagged FLAC and MP3 files of all included tracks."""
        self.__log.call()

        if self.encoding_status_frame is not None:
            self.encoding_status_frame.destroy()
            self.encoding_status_frame = None

        self.disc_eject_button.config(state=tk.DISABLED)
        self.rip_and_tag_button.config(state=tk.DISABLED)

        self._persist_metadata() # issues/1

        try:
            encoder = self._prepare_encoder()
        except Exception as e:
            self.__log.exception("failed to initialize the encoder")
            show_exception_dialog(e)
            self.disc_eject_button.config(state=tk.NORMAL)
            self.rip_and_tag_button.config(state=tk.NORMAL)
        else:
            encoder.start()
            self.__log.info("encoding has started; monitoring progress...")
            self._monitor_encoding_progress()

    def _prepare_encoder(self):
        """Create and initialize the object that will encode the album
        tracks.

        :return:
           a :class:`FLACEncoder` populated with encoding instructions
           for each track

        """
        self.__log.call()

        disc_filenames = [
            name for name in os.listdir(self._mountpoint)
            if not name.startswith('.') and
                os.path.splitext(name)[1] in
                    [".aiff", ".aif", ".aifc", ".cdda", ".cda"]]
        if len(disc_filenames) != len(self.toc.track_offsets):
            raise FLACManagerError(
                "Disc TOC reported %d tracks, but %d files were found at "
                        "mount point %s" % (
                    len(self.toc.track_offsets), len(trackfiles),
                    self._mountpoint),
                context_hint="FLAC ripping")

        flac_library_root = get_config().get("FLAC", "library_root")
        try:
            flac_library_root = resolve_path(flac_library_root)
        except Exception as e:
            raise FLACManagerError(
                "Cannot use FLAC library root %s: %s" % (flac_library_root, e),
                context_hint="FLAC ripping", cause=e)

        mp3_library_root = get_config().get("MP3", "library_root")
        try:
            mp3_library_root = resolve_path(mp3_library_root)
        except Exception as e:
            raise FLACManagerError(
                "Cannot use MP3 library root %s: %s" % (mp3_library_root, e),
                context_hint="MP3 encoding", cause=e)

        self.encoding_status_frame = self._create_encoder_status(self)
        self.metadata_editor.pack_forget()
        self.encoding_status_frame.pack(
            fill=tk.BOTH, padx=17, pady=17, expand=tk.YES)

        encoder = FLACEncoder()
        tracks_metadata = self._prepare_tagging_metadata()
        for (index, metadata) in enumerate(tracks_metadata):
            if metadata is None:
                continue
            cdda_filename = os.path.join(
                self._mountpoint, disc_filenames[index])

            flac_dirname = generate_flac_dirname(flac_library_root, metadata)
            flac_basename = generate_flac_basename(metadata)
            flac_filename = os.path.join(flac_dirname, flac_basename)
            self.__log.info("%s -> %s", cdda_filename, flac_filename)

            mp3_dirname = generate_mp3_dirname(mp3_library_root, metadata)
            mp3_basename = generate_mp3_basename(metadata)
            mp3_filename = os.path.join(mp3_dirname, mp3_basename)
            self.__log.info("%s -> %s", flac_filename, mp3_filename)

            encoder.add_instruction(
                index, cdda_filename, flac_filename, mp3_filename, metadata)

        self.__log.return_(encoder)
        return encoder

    def _monitor_encoding_progress(self):
        """Update the UI as tracks are ripped."""
        # don't log entry into this method - it is called repeatedly until all
        # tracks are ripped
        try:
            (priority, status) = _ENCODING_QUEUE.get_nowait()
        except queue.Empty:
            self.after(QUEUE_GET_NOWAIT_AFTER, self._monitor_encoding_progress)
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

                self.disc_eject_button.config(state=tk.NORMAL)
                self.rip_and_tag_button.pack_forget()
                self.rip_and_tag_button.config(state=tk.NORMAL)

                self.master.bell()
                self.__log.trace("break out of the monitoring loop")
                return

            track_label = self.track_labels[track_index]
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
                    self._encoding_status_list.see(track_index)
                    # read encoding interval status from flac's stdout
                    cdda_basename = os.path.basename(cdda_fn)
                    stdout_message = self._read_current_status(
                        cdda_basename, stdout_fn)
                    status_message = track_encoding_status.describe(
                        message=stdout_message if stdout_message else None)
                    item_config = {"fg": "blue"}
                elif track_encoding_status.state in [
                        TRACK_DECODING_WAV,
                        TRACK_ENCODING_MP3, TRACK_REENCODING_MP3]:
                    status_message = track_encoding_status.describe()
                    item_config = {"fg": "dark violet"}
                elif track_encoding_status.state == TRACK_COMPLETE:
                    status_message = flac_fn
                    item_config = {"fg": "dark green"}
                else:   # unexpected state
                    status_message = "%s (unexpected target state %s)" % (
                        track_encoding_status.describe(), target_state)
                    item_config = {"fg": "red"}

                self._encoding_status_list.delete(track_index)
                self._encoding_status_list.insert(track_index, status_message)
                self._encoding_status_list.itemconfig(track_index, item_config)

                # ensure that last track is always visible after delete/insert
                if (track_index ==
                        self._encoding_status_list.index(tk.END) - 1):
                    self._encoding_status_list.see(track_index)

            self.after(QUEUE_GET_NOWAIT_AFTER, self._monitor_encoding_progress)

    def _read_current_status(self, cdda_basename, stdout_fn):
        """Extract the most recent FLAC encoding update from
        *stdout_fn*.

        :param str cdda_basename: a grep pattern for *stdout_fn*
        :param str stdout_fn:
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

    def _destroy_metadata_editor(self):
        """Cleanup up UI resources created for the metadata editor."""
        self.__log.call()

        if self.metadata_editor is not None:
            self.metadata_editor.destroy()
        self.metadata_editor = None
        self._persistence = None
        self._album_metadata = None
        self._tracks_metadata = None

        self.album_title_var = None
        self.album_artist_var = None
        self.album_performer_var = None
        self.album_recordlabel_var = None
        self.album_genre_var = None
        self.album_year_var = None
        self.album_disc_number_var = None
        self.album_disc_total_var = None
        self.album_cover_var = None
        self._album_covers = None

        self.track_title_entry = None
        self._track_title_optionmenu = None
        self.track_artist_entry = None
        self._track_artist_optionmenu = None
        self.track_performer_entry = None
        self._track_performer_optionmenu = None
        self.track_genre_entry = None
        self._track_genre_optionmenu = None
        self.track_year_entry = None
        self._track_year_optionmenu = None
        self._track_vars = None

    def _do_disc_check(self):
        """Spawn the :class:`DiscCheck` thread."""
        self.__log.call()

        self.retry_disc_check_button.pack_forget()
        DiscCheck().start()
        self._check_for_disc()

    def _check_for_disc(self):
        """Update the UI if a CD-DA disc is present.

        If a disc is **not** present, set a UI timer to check again.

        """
        # do not trace; called indefinitely until a disc is found
        try:
            disc_info = _DISC_QUEUE.get_nowait()
        except queue.Empty:
            self.after(QUEUE_GET_NOWAIT_AFTER, self._check_for_disc)
        else:
            _DISC_QUEUE.task_done()
            self.__log.debug("dequeued %r", disc_info)
            (self._disk, self._mountpoint) = disc_info

            if isinstance(disc_info, Exception):
                self.__log.error("dequeued %r", disc_info)
                show_exception_dialog(disc_info)
                self.retry_disc_check_button.pack(
                    side=tk.RIGHT, padx=7, pady=5)
                return None

            self.disc_status_message.pack_forget()
            self.disc_status_message.config(text=self._mountpoint)
            self.disc_eject_button.pack(side=tk.LEFT, padx=7, pady=5)
            self.disc_status_message.pack(side=tk.LEFT)

            self.toc = read_disc_toc(self._mountpoint)
            # once we have the TOC, it's ok for the disc to be ejected (though,
            # of course, if it's ejected immediately it can't be ripped)
            self.disc_eject_button.config(state=tk.NORMAL)

            self._do_metadata_aggregation()

    def _do_metadata_aggregation(self):
        """Spawn the :class:`MetadataAggregator` thread."""
        self.__log.call()

        try:
            MetadataAggregator(self.toc).start()
        except Exception as e:
            self.__log.exception("failed to start metadata aggregator")
            show_exception_dialog(e)
            self.retry_aggregation_button.pack()
            self.edit_offline_button.pack()
        else:
            self.retry_aggregation_button.pack_forget()
            self.edit_offline_button.pack_forget()
            self.status_message.pack(fill=tk.BOTH)
            self._check_for_aggregator()

    def _check_for_aggregator(self):
        """Update the UI if aggregated metadata is ready.

        If aggregated metadata is **not** ready, set a UI timer to check
        again.

        """
        # don't log entry into this method - it calls itself recursively until
        # the aggregated metadata is ready
        try:
            aggregator = _AGGREGATOR_QUEUE.get_nowait()
        except queue.Empty:
            self.after(500, self._check_for_aggregator)
        else:
            self.__log.debug("dequeued %r", aggregator)
            _AGGREGATOR_QUEUE.task_done()

            # set these whether an error occurred or not - they're needed by
            # offline editing mode as well
            self._persistence = aggregator.persistence
            self._album_metadata = aggregator.album
            self._tracks_metadata = aggregator.tracks

            if aggregator.exception is None:
                try:
                    self._create_metadata_editor()
                except Exception as e:
                    self.__log.exception("failed to create metadata editor")
                    show_exception_dialog(e)
                    self.status_message.pack_forget()
                    self.retry_aggregation_button.pack()
                    self.edit_offline_button.pack()
            else:
                show_exception_dialog(aggregator.exception)
                self.status_message.pack_forget()
                self.retry_aggregation_button.pack()
                self.edit_offline_button.pack()


    def _eject_disc(self):
        """Eject the current CD-DA disc and update the UI."""
        self.__log.call()

        status = subprocess.call(
            ["diskutil", "eject", self._disk],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if status == 0:
            self.__log.info(
                "ejected %s mounted at %s", self._disk, self._mountpoint)

            self.rip_and_tag_button.pack_forget()
            self._destroy_metadata_editor()

            if self.encoding_status_frame is not None:
                self.encoding_status_frame.destroy()
                self.encoding_status_frame = None
                self._encoding_status_list.destroy()
                self._encoding_status_list = None

            self._disk = self._mountpoint = None

            self.disc_eject_button.pack_forget()
            self.retry_aggregation_button.pack_forget()
            self.edit_offline_button.pack_forget()
            self.status_message.pack_forget()

            self.toc = None
            self.disc_status_message.config(
                text="Waiting for a disc to be inserted\u2026",
                padx=5, pady=3)

            DiscCheck().start()
            self._check_for_disc()
        else:
            self.__log.error(
                "unable to eject %s mounted at %s",
                self._disk, self._mountpoint)
            messagebox.showerror(
                title="Disk eject failure",
                message="Unable to eject %s" % self._mountpoint)

    def _edit_aggregation_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditAggregationConfigurationDialog(
            self.master, title="Edit flacmanager.ini (metadata aggregation)")

    def _edit_organization_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditOrganizationConfigurationDialog(
            self.master,
            title="Edit flacmanager.ini (default folder and file names)")

    def _edit_flac_encoding_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditFLACEncodingConfigurationDialog(
            self.master, title="Edit flacmanager.ini (FLAC encoding)")

    def _edit_vorbis_comments_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditVorbisCommentsConfigurationDialog(
            self.master,
            title="Edit flacmanager.ini (default FLAC Vorbis comments)")

    def _edit_flac_organization_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditFLACOrganizationConfigurationDialog(
            self.master,
            title="Edit flacmanager.ini (FLAC folder and file names)")

    def _edit_mp3_encoding_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditMP3EncodingConfigurationDialog(
            self.master, title="Edit flacmanager.ini (MP3 encoding)")

    def _edit_id3v2_tags_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditID3v2TagsConfigurationDialog(
            self.master, title="Edit flacmanager.ini (default MP3 ID3v2 tags)")

    def _edit_mp3_organization_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditMP3OrganizationConfigurationDialog(
            self.master,
            title="Edit flacmanager.ini (MP3 folder and file names)")

    def _edit_logging_config(self):
        """Open the configuration editor dialog."""
        self.__log.mark()
        EditLoggingConfigurationDialog(
            self.master, title="Edit flacmanager.ini (logging/debug)")

    def prerequisites(self):
        """Open the prerequisites information dialog."""
        self.__log.mark()
        PrerequisitesDialog(self.master, title="%s prerequisites" % self.TITLE)

    def about(self):
        """Open the application description dialog."""
        self.__log.mark()
        AboutDialog(self.master, title="About %s" % self.TITLE)

    def _exit(self):
        """Quit the FLACManager application."""
        self.master.withdraw()
        self.destroy()
        self.master.quit()


@total_ordering
class TrackState:
    """Represents the state of a single track at any given time during
    the rip-and-tag operation.

    """

    def __init__(self, ordinal, key, text):
        """
        :param int ordinal: this state's relative value
        :param str key: uniquely identifies this state
        :param str text: a short description of this state

        """
        self._ordinal = ordinal
        self._key = key
        self._text = text

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

        :param flacmanager.TrackState other: the state being compared

        """
        return int(self) < int(other)

    def __eq__(self, other):
        """Return ``True`` if this state's ordinal value and key are
        equal to *other* state's ordinal value and key, otherwise
        ``False``.

        :param flacmanager.TrackState other: the state being compared

        """
        return (
            isinstance(other, self.__class__) and
            self._ordinal == other._ordinal and
            self._key == other._key)

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

#: Indicates that a track is being re-encoded from WAV to MP3 format after
#: clipping was detected in a prior encoding operation.
TRACK_REENCODING_MP3 = TrackState(
    4, "REENCODING_MP3", "re-encoding MP3 (clipping detected)\u2026")

#: Indicates that an error occurred while processing a track.
TRACK_FAILED = TrackState(99, "FAILED", "failed")

#: Indicates that the rip-and-tag process has finished for a track.
TRACK_COMPLETE = TrackState(99, "COMPLETE", "complete")


@logged
class TrackEncodingStatus:
    """A simple state machine for a single track's encoding status."""

    def __init__(self, track_label, pending=True):
        """
        :param str track_label: the track's display label
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

        :param to_state:
           the target encoding state, or any :class:`Exception` to
           transition to :data:`TRACK_FAILED`
        :return:
           ``True`` if the transition is successful, otherwise ``False``

        """
        self.__log.call(to_state)

        from_state = self.__state
        if isinstance(to_state, Exception):
            to_state = TRACK_FAILED

        if (from_state in [TRACK_EXCLUDED, TRACK_FAILED, TRACK_COMPLETE] or
                to_state < from_state):
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

        :param str message:
           short piece of text to use with the track label (instead of
           the default message for the current state)

        """
        return "%s: %s" % (
            self.track_label,
            message if message is not None else self.__state.text)


def generate_flac_dirname(library_root, metadata):
    """Build the directory for a track's FLAC file.

    :param str library_root: the FLAC library directory
    :param dict metadata: the finalized metadata for a single track
    :return: an absolute directory path
    :rtype: :obj:`str`

    """
    _log.mark()
    return _generate_dirname("FLAC", library_root, metadata)


def generate_flac_basename(metadata):
    """Build the filename for a track's FLAC file.

    :param dict metadata: the finalized metadata for a single track
    :return: a relative file name
    :rtype: :obj:`str`

    """
    _log.mark()
    return _generate_basename("FLAC", metadata)


def generate_mp3_dirname(library_root, metadata):
    """Build the directory for a track's MP3 file.

    :param str library_root: the MP3 library directory
    :param dict metadata: the finalized metadata for a single track
    :return: an absolute directory path
    :rtype: :obj:`str`

    """
    _log.mark()
    return _generate_dirname("MP3", library_root, metadata)


def generate_mp3_basename(metadata):
    """Build the filename for a track's MP3 file.

    :param dict metadata: the finalized metadata for a single track
    :return: a relative file name
    :rtype: :obj:`str`

    """
    _log.mark()
    return _generate_basename("MP3", metadata)


def _generate_dirname(section, library_root, metadata):
    """Build the directory for a track's FLAC or MP3 file.

    :param str section: "FLAC" or "MP3"
    :param str library_root: the MP3 library directory
    :param dict metadata: the finalized metadata for a single track
    :return: an absolute directory path
    :rtype: :obj:`str`

    """
    _log.call(section, library_root, metadata)

    config = get_config()

    ndisc = "ndisc_" if metadata["disc_total"] > 1 else ""
    is_compilation = metadata["is_compilation"]
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

    :param str section: "FLAC" or "MP3"
    :param dict metadata: the finalized metadata for a single track
    :return: a relative file name
    :rtype: :obj:`str`

    """
    _log.call(section, metadata)

    config = get_config()

    ndisc = "ndisc_" if metadata["disc_total"] > 1 else ""
    is_compilation = metadata["is_compilation"]
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

    :param path:
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

    :param str section: "FLAC" or "MP3"
    :param dict metadata: the finalized metadata for a single track
    :return:
       a list (possibly empty) of directory names that form a trie
       structure for organizing audio files

    """
    _log.call(metadata)

    config = get_config()

    key = (
        config[section]["library_subroot_trie_key"]
        if not metadata["is_compilation"] else
        config[section]["library_subroot_compilation_trie_key"])
    level = config[section].getint("library_subroot_trie_level")

    # to skip building a directory trie structure, the key can be left empty or
    # the level can be set to zero (0)
    if not key or level <= 0:
        _log.trace("RETURN []")
        return []

    term = re.sub(r"[^0-9a-zA-Z]", "", metadata[key]).upper()
    # use len(term) - 1 so trie prefixes never include the full term
    nodes = [term[:n + 1] for n in range(min(level, len(term) - 1))]
    # edge case - any non-alphanumeric key falls into the special '_' node
    if not nodes:
        nodes = ['_']

    _log.return_(nodes)
    return nodes


def _font(widget):
    """Proxy *widget*'s font so that it can be configured.

    :param tkinter.Widget widget: any widget
    :return: a configurable font object for *widget*
    :rtype: :class:`tkfont.Font`

    This is a helper function to allow the following shorthand::

       _font(widget).config(**keywords)

    .. note::
       Updates to a :class:`tkinter.font.Font` done in this way affect
       **only** the *widget*.

    """
    font = tkfont.Font(widget, font=widget["font"])
    widget.config(font=font)
    return font


class PrerequisitesDialog(simpledialog.Dialog):
    """A dialog that describes all FLAC Manager prerequisites."""

    #: The content of the dialog.
    TEXT = (
        "%(title)s runs on Python 3.3+.\n\n"
        "%(title)s requires the following external software components:\n"
        "* libdiscid (http://musicbrainz.org/doc/libdiscid)\n"
        "* flac (http://flac.sourceforge.net/)\n"
        "* lame (http://lame.sourceforge.net/)\n\n"
        "The flac and lame command-line binaries must exist on your $PATH. "
        "Each of these components is also available through MacPorts "
        "(http://www.macports.org/).\n\n"
        "In addition to the software listed above, %(title)s relies on the "
        "following Mac OS X command line utilties:\n"
        "* diskutil\n"
        "* open\n"
        "* mkdir\n\n"
        "You must register for a Gracenote developer account "
        "(https://developer.gracenote.com/) in order for %(title)s's metadata "
        "aggregation to function properly:\n"
        "1. Create your Gracenote developer account.\n"
        "2. Create an app named \"%(title)s.\"\n"
        "3. Save your Gracenote Client ID in %(title)s's configuration file.\n"
        "\n%(title)s will automatically obtain and store the Gracenote User "
        "ID in the flacmanager.ini file.\n\n"
    ) % dict(title=FLACManager.TITLE)

    def body(self, frame):
        """Create the content of the dialog."""
        text = scrolledtext.ScrolledText(
            frame, height=11, bd=0, relief=tk.FLAT, wrap=tk.WORD)
        text.insert(tk.END, self.TEXT)
        text.pack()
        text.focus_set()

    def buttonbox(self):
        """Create the button to dismiss the dialog."""
        box = tk.Frame(self)
        tk.Button(
            box, text="OK", width=11, command=self.ok,
            default=tk.ACTIVE).pack(padx=5, pady=5)
        self.bind("<Return>", self.ok)
        box.pack()


class AboutDialog(simpledialog.Dialog):
    """A dialog that describes FLAC Manager."""

    def body(self, frame):
        """Create the content of the dialog."""
        title_label = tk.Label(
            frame, text="%s v%s\n" % (FLACManager.TITLE, __version__),
            fg="DarkOrange2")
        _font(title_label).config(size=19, weight=tkfont.BOLD)
        title_label.pack()

        text = scrolledtext.ScrolledText(
            frame, height=11, bd=0, relief=tk.FLAT)
        with open("LICENSE.txt", 'r') as f:
            text.insert(tk.END, f.read())
        text.pack()
        text.focus_set()

    def buttonbox(self):
        """Create the button to dismiss the dialog."""
        box = tk.Frame(self)
        tk.Button(
            box, text="OK", width=11, command=self.ok,
            default=tk.ACTIVE).pack(padx=5, pady=5)
        self.bind("<Return>", self.ok)
        box.pack()


class _EditConfigurationDialog(simpledialog.Dialog):
    """Base class for dialogs that allow the user to edit the
    *flacmanager.ini* configuration file.

    """

    def body(self, frame):
        """Create the content of the dialog.

        :param tk.Frame frame: the frame that contains the body content

        """
        self._row = 0
        self._variables = {} # "Section" -> { "option" -> tk-variable }

        self._populate(frame, get_config())

    def section(self, parent, section_name):
        """Add a section header to the body of the dialog.

        :param parent: the parent object of the section header
        :param str section_name: the name of the configuration section

        """
        section_label = tk.Label(parent, text=section_name)
        _font(section_label).config(size=13, weight=tkfont.BOLD)
        section_label.grid(row=self._row, columnspan=2, pady=11, sticky=tk.W)

        self._row += 1

    def option(
            self, parent, section_name, option_name, value, width=67):
        """Add an editable option to the body of the dialog.

        :param parent: the parent object of the section header
        :param str section_name: the name of the configuration section
        :param str option_name: the name of the configuration option
        :param value: the default/initial value of the option
        :keyword int width:
           the display width of the entry box for this option's value

        """
        tk.Label(parent, text=option_name).grid(row=self._row, sticky=tk.E)

        if type(value) is int:
            variable = tk.IntVar(self, value=value)
        elif type(value) is bool:
            variable = tk.BooleanVar(self, value=value)
        elif type(value) is float:
            variable = tk.DoubleVar(self, value=value)
        else:
            variable = tk.StringVar(
                self, value=value if type(value) is not list else value[0])

        if type(value) is list:
            tk.OptionMenu(parent, variable, *value[1:]).grid(
                row=self._row, column=1, sticky=tk.W)
        elif type(variable) is tk.BooleanVar:
            tk.Checkbutton(
                parent, variable=variable, onvalue=True, offvalue=False).grid(
                    row=self._row, column=1, sticky=tk.W)
        else:
            tk.Entry(parent, textvariable=variable, width=width).grid(
                row=self._row, column=1, sticky=tk.W)

        self._row += 1

        # track the variable; see apply()
        if section_name not in self._variables:
            self._variables[section_name] = {}
        self._variables[section_name][option_name] = variable

    def buttonbox(self):
        """Create the buttons to save and/or dismiss the dialog."""
        box = tk.Frame(self)

        tk.Button(
            box, text="Save", width=10, command=self.ok, default=tk.ACTIVE
            ).pack(side=tk.LEFT, padx=5, pady=5)

        tk.Button(
            box, text="Cancel", width=10,
            command=self.cancel
            ).pack(side=tk.LEFT, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack()

    def apply(self):
        """Save changes to the *flacmanager.ini* file."""
        config = get_config()

        for (section, optvar) in self._variables.items():
            for (option, variable) in optvar.items():
                # values MUST be strings!
                if type(variable) is not tk.BooleanVar:
                    config[section][option] = str(variable.get())
                else:
                    config[section][option] = "yes" if variable.get() else "no"

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
        tk.Label(
            frame, text="${Organize:library_subroot_trie_level} or a number"
            ).grid(row=self._row, column=1, sticky=tk.W)
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
        tk.Label(
            frame, text="${Organize:use_xplatform_safe_names}, yes, or no"
            ).grid(row=self._row, column=1, sticky=tk.W)
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
        tk.Label(
            frame, text="${Organize:library_subroot_trie_level} or a number"
            ).grid(row=self._row, column=1, sticky=tk.W)
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
        tk.Label(
            frame, text="${Organize:use_xplatform_safe_names}, yes, or no"
            ).grid(row=self._row, column=1, sticky=tk.W)
        self._row += 1


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


@logged
class EditCustomMetadataTaggingDialog(simpledialog.Dialog):
    """Base dialog that allows the user to add/change/remove custom
    metadata fields for Vorbis/ID3v2 tagging.

    """

    def __init__(self, master, metadata, **keywords):
        """Initialize the dialog.

        :param master: the parent object of this dialog
        :param metadata: the album or track metadata
        :param dict keywords:
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

        :param tk.Frame frame: the frame that contains the body content

        """
        self.__log.mark()

        self._row = 0

        self._instructions_var = tk.StringVar()
        self._body_instructions()

        tk.Label(
            frame, textvariable=self._instructions_var,
            anchor=tk.NW, justify=tk.LEFT,
        ).grid(row=self._row, columnspan=4, pady=7, sticky=tk.W)
        self._row += 1

        add_button = tk.Button(
            frame, text="Add", fg="Blue", activeforeground="Blue",
            command=lambda f=self._add_field, p=frame: f(p),
            default=tk.ACTIVE
            )
        _font(add_button).configure(size=11, weight=tkfont.BOLD)
        add_button.grid(row=self._row, column=0, pady=7, sticky=tk.W)

        vorbis_label = tk.Label(frame, text="Vorbis")
        _font(vorbis_label).config(size=11, weight=tkfont.BOLD)
        vorbis_label.grid(row=self._row, column=1, pady=5, sticky=tk.W)

        id3v2_label = tk.Label(frame, text="ID3v2")
        _font(id3v2_label).config(size=11, weight=tkfont.BOLD)
        id3v2_label.grid(row=self._row, column=2, pady=5, sticky=tk.W)

        value_label = tk.Label(frame, text="Value")
        _font(value_label).config(size=11, weight=tkfont.BOLD)
        value_label.grid(row=self._row, column=3, pady=5, sticky=tk.W)

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

        :param parent: the object that contains the field controls
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

            clear_button = tk.Button(
                parent, text="\u00d7", fg="Red", activeforeground="Red",
                command=lambda f=self._clear_field, ix=fields_ix: f(ix)
            )
            _font(clear_button).config(weight=tkfont.BOLD)
            clear_button.grid(row=self._row, column=0)

            vorbis_var = tk.StringVar(parent, value=vorbis_comment)
            vorbis_entry = tk.Entry(parent, textvariable=vorbis_var, width=17)
            vorbis_entry.grid(row=self._row, column=1, sticky=tk.W)

            id3v2_var = tk.StringVar(parent, value=id3v2_tag)
            id3v2_entry = tk.Entry(parent, textvariable=id3v2_var, width=7)
            id3v2_entry.grid(row=self._row, column=2, sticky=tk.W)

            value_var = tk.StringVar(parent, value=value)
            value_entry = tk.Entry(parent, textvariable=value_var, width=59)
            value_entry.grid(row=self._row, column=3, sticky=tk.W)

            self._fields.append((vorbis_var, id3v2_var, value_var))
            self._widgets.append(
                (clear_button, vorbis_entry, id3v2_entry, value_entry))

            self._row += 1

    def _clear_field(self, index):
        """Clear (effectively removing) the *index* -th field.

        :param int index: index into the *fields* list of variables

        """
        self.__log.call(index)

        for var in self._fields[index]:
            var.set("")
        for i in range(4):
            self._widgets[index][i].destroy()
            

    def buttonbox(self):
        """Create the buttons to save and/or dismiss the dialog."""
        box = tk.Frame(self)

        tk.Button(box, text="Save", width=10, command=self.ok).pack(
            side=tk.LEFT, padx=5, pady=5)

        tk.Button(box, text="Cancel", width=10, command=self.cancel).pack(
            side=tk.LEFT, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack()

    def apply(self):
        """Save changes to *metadata*."""
        self.__log.mark()

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

        :param master: the parent object of this dialog
        :param album_metadata: the album metadata
        :param tracks_metadata: the track metadata
        :param dict keywords:
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

        :param parent: the object that contains the field controls
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
            for widget in self._widgets[-1][1:]:
                widget.config(state=tk.DISABLED)

    def _clear_field(self, index):
        """Clear (effectively removing) the *index* -th field.

        :param int index: index into the *fields* list of variables

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
        self.__log.mark()

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

        :param dict track_custom:
           the custom metadata tagging fields for track *i*
        :param int i:
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

    :param str spec: a directory or file path template
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

    :param str cdda_filename: absolute CD-DA file name
    :param str flac_filename: absolute *.flac* file name
    :param dict track_metadata: tagging fields for this track
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

    :param str flac_filename: absolute *.flac* file name
    :param str wav_filename: absolute *.wav* file name
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

    :param str wav_filename: absolute *.wav* file name
    :param str mp3_filename: absolute *.mp3* file name
    :param dict track_metadata: tagging fields for this track
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
    for (name, values) in id3v2_tags.items():
        if not values:
            continue
        # ID3v2 spec calls for '/' separator, but iTunes only handles ','
        # separator correctly
        command.extend(["--tv", "%s=%s" % (name, ", ".join(values))])

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

    :param dict metadata: the metadata for a single track
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
    _log.mark()

    comments = _make_tagging_map("Vorbis", metadata)

    # flac automatically includes a vendor string to identify itself
    comments["ENCODER"] = [
        "http://ninthtest.net/flac-mp3-audio-manager/ %s" % __version__]

    _log.return_(comments)
    return comments


def make_id3v2_tags(metadata):
    """Create ID3v2 frames for tagging from *metadata*.

    :param dict metadata: the metadata for a single track
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
    _log.mark()

    tags = _make_tagging_map("ID3v2", metadata)

    # lame automatically includes TSSE to identify itself
    tags["TENC"] = ["http://ninthtest.net/flac-mp3-audio-manager/"]

    _log.return_(tags)
    return tags


def _make_tagging_map(type_, metadata):
    """Create Vorbis comments or ID3v2 frames for tagging from
    *metadata*.

    :param str type_:
       "Vorbis" or "ID3v2" (corresponds to a tagging section in the
       flacmanager.ini configuration file)
    :param dict metadata: the metadata for a single track
    :return: Vorbis commen or ID3v2 frame name/value pairs
    :rtype: :obj:`dict`

    """
    _log.call(metadata)

    config = get_config()

    tags = OrderedDict()
    for (tag, spec) in config[type_].items():
        if spec[0] == '{': # format specification
            formatted = spec.format(**metadata)
            value = [formatted] if formatted else None
        else: # direct key lookup
            value = metadata[spec] if type(metadata[spec]) is list \
                else [metadata[spec]]

        # ignore empty values
        if value:
            tags[tag] = value

    _update_custom_tagging(tags, type_, metadata)

    _log.return_(tags)
    return tags


def _update_custom_tagging(tags, type_, metadata):
    """Update *tags* with any custom Vorbis comments or ID3v2 tags from
    *metadata["__custom"]* (if defined).

    :param dict tags: the tagging map for a track
    :param str type_: "Vorbis" or "ID3v2"
    :param dict metadata: the metadata for a single track

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

#: Used to pass data between a :class:`FLACEncoder` thread and the main thread.
_ENCODING_QUEUE = queue.PriorityQueue()


#: The number of seconds to wait between enqueuing a FLAC encoding status.
FLAC_ENCODING_STATUS_WAIT = 1.25


@logged
class FLACEncoder(threading.Thread):
    """A thread that rips CD-DA tracks to FLAC."""

    def __init__(self):
        self.__log.call()
        super().__init__(daemon=True)

        self._instructions = []

    def add_instruction(self, track_index, cdda_filename, flac_filename,
                        mp3_filename, track_metadata):
        """Schedule a track for FLAC encoding.

        :param int track_index: index (not ordinal) of the track
        :param str cdda_filename: absolute CD-DA file name
        :param str flac_filename: absolute *.flac* file name
        :param str mp3_filename: absolute *.mp3* file name
        :param dict track_metadata: tagging fields for this track

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

        :param int track_index: index (**not** ordinal) of the track
        :param str cdda_filename: absolute CD-DA file name
        :param str flac_filename: absolute .flac file name
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
        :param int track_index: index (not ordinal) of the track
        :param str cdda_filename: absolute CD-DA file name
        :param str flac_filename: absolute *.flac* file name
        :param str stdout_filename:
           absolute file name for redirected stdout
        :param dict track_metadata: tagging fields for this track

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
                    self.stdout_filename, TRACK_REENCODING_MP3)
                _ENCODING_QUEUE.put((5, status))

                encode_mp3(
                    wav_filename, self.mp3_filename, self.track_metadata,
                    scale=scale, stdout_filename=self.stdout_filename)

                clipping_occurs = (
                    "WARNING: clipping occurs at the current gain."
                    in self.__read_stdout())
                scale -= 0.01

    def __read_stdout(self):
        with open(self.stdout_filename) as f:
            return f.read()


class MetadataError(FLACManagerError):
    """The type of exception raised when metadata operations fail."""


@logged
class MetadataCollector:
    """Base class for collecting album and track metadata."""

    def __init__(self, toc):
        """
        :param flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)

        self.toc = toc

    def reset(self):
        """Initialize all collection fields to default (empty)."""
        self.__log.call()

        number_of_tracks = len(self.toc.track_offsets)
        self.album = {
            "title": [],
            "artist": [],
            "performer": [],
            "record_label": [],
            "year": [],
            "genre": [],
            "cover": [],
            "number_of_tracks": number_of_tracks,
            "is_compilation": False,
            "disc_number": 1,
            "disc_total": 1,
        }
       
        tracks = [None] # 1-based indexing
        for i in range(number_of_tracks):
            tracks.append({
                "include": True,
                "number": i + 1,
                "title": [],
                "artist": [],
                "performer": [],
                "year": [],
                "genre": [],
            })
        self.tracks = tracks

    def collect(self):
        """Fetch metadata from a service."""
        self.reset()


@logged
class GracenoteCDDBMetadataCollector(MetadataCollector):
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
        :param flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)
        super().__init__(toc)

        config = get_config()
        self.__log.debug("Gracenote config = %r", dict(config["Gracenote"]))

        self._client_id = config.get("Gracenote", "client_id")
        if not self._client_id:
            raise MetadataError(
                    "Gracenote client_id must be defined in flacmanager.ini!",
                    context_hint="Gracenote configuration")

        self._user_id = config.get("Gracenote", "user_id")

        api_host = self.API_HOST_TEMPLATE % self._client_id.split('-', 1)[0]
        self.timeout = config.getfloat("HTTP", "timeout")
        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
        self._ssl_context.verify_mode = ssl.CERT_NONE
        self._ssl_context.set_default_verify_paths()
        self._conx = HTTPSConnection(
            api_host, context=self._ssl_context, timeout=self.timeout)

    def _register(self):
        """Register this client with the Gracenote Web API."""
        self.__log.call()

        gn_queries = ET.fromstring(self.REGISTER_XML)
        gn_queries.find("QUERY/CLIENT").text = self._client_id

        gn_responses = self._get_response(gn_queries)
        user = gn_responses.find("RESPONSE/USER")
        self._user_id = user.text
        self.__log.debug("user_id = %r", self._user_id)

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
        album_metadata = self.album
        tracks_metadata = self.tracks
        for gn_album_summary in gn_responses.findall("RESPONSE/ALBUM"):
            gn_id = gn_album_summary.find("GN_ID").text
            gn_album_detail = self._fetch_album(
                gn_id, album_ord == last_album_ord)

            num_tracks = int(gn_album_detail.find("TRACK_COUNT").text)
            if num_tracks != album_metadata["number_of_tracks"]:
                self.__log.warning(
                    "discarding %r; expected %d tracks but found %d",
                    gn_id, album_metadata["number_of_tracks"], num_tracks)
                continue

            title = gn_album_detail.find("TITLE").text
            if title not in album_metadata["title"]:
                album_metadata["title"].append(title)

            artist = gn_album_detail.find("ARTIST").text
            if artist not in album_metadata["artist"]:
                album_metadata["artist"].append(artist)

            gn_date = gn_album_detail.find("DATE")
            if (gn_date is not None and
                    gn_date.text not in album_metadata["year"]):
                album_metadata["year"].append(gn_date.text)

            for gn_genre in gn_album_detail.findall("GENRE"):
                genre = gn_genre.text
                if genre not in album_metadata["genre"]:
                    album_metadata["genre"].append(genre)

            for gn_url_coverart in gn_album_detail.findall(
                    "URL[@TYPE='COVERART']"):
                cover_art = self._get_cover_image(gn_url_coverart.text)
                if cover_art and cover_art not in album_metadata["cover"]:
                    album_metadata["cover"].append(cover_art)

            for gn_track in gn_album_detail.findall("TRACK"):
                track_number = int(gn_track.find("TRACK_NUM").text)
                track_metadata = tracks_metadata[track_number]

                title = gn_track.find("TITLE").text
                if title not in track_metadata["title"]:
                    track_metadata["title"].append(title)

                gn_artist = gn_track.find("ARTIST")
                if (gn_artist is not None and
                        gn_artist.text not in track_metadata["artist"]):
                    track_metadata["artist"].append(gn_artist.text)

                for gn_genre in gn_track.findall("GENRE"):
                    genre = gn_genre.text
                    if genre not in track_metadata["genre"]:
                        track_metadata["genre"].append(genre)

            album_ord += 1

        album_metadata["performer"] = list(album_metadata["artist"])
        for track_metadata in tracks_metadata[1:]:
            track_metadata["performer"] = list(track_metadata["artist"])

    def _fetch_album(self, gn_id, is_last_album=True):
        """Make a Gracenote 'ALBUM_FETCH' request.

        :param str gn_id: the Gracenote ID of an album
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

    def _get_cover_image(self, url):
        """Fetch a Gracenote album cover image over HTTP.

        :param str url: the URL for a Gracenote album cover
        :return: the raw image :obj:`bytes` data

        """
        self.__log.call(url)

        parse_result = urlparse(url)
        host = parse_result.netloc

        if parse_result.scheme == "https":
            conx = HTTPSConnection(
                host, context=self._ssl_context, timeout=self.timeout)
        elif parse_result.scheme == "http":
            conx = HTTPConnection(host, timeout=self.timeout)
        else:
            self.__log.warning(
                "don't know how to request an image over %s",
                parse_result.scheme)
            return None

        conx.request("GET", "%s?%s" % (parse_result.path, parse_result.query))
        response = conx.getresponse()
        while response.status in [301, 302, 307]:
            response.close()

            url = urlparse(response.headers["Location"])
            if url.netloc != host:
                conx.close()
                if url.scheme == "https":
                    conx = HTTPSConnection(
                        url.netloc, context=self._ssl_context,
                        timeout=self.timeout)
                else:
                    conx = HTTPConnection(
                        parse_result.netloc, timeout=self.timeout)

            path = \
                url.path if not url.query else "%s?%s" % (url.path, url.query)
            conx.request("GET", path)

            response = conx.getresponse()

        data = response.read()
        response.close()
        conx.close()

        if response.status != 200:
            if "Content-Type" in response.headers:
                (_, params) = cgi.parse_header(
                    response.headers["Content-Type"])
                encoding = params.get("charset", "UTF-8")
            else:
                encoding = "UTF-8"
            self.__log.warning(
                "unable to get cover art from %r (HTTP %d %s: %s)",
                url, response.status, response.reason, data.decode(encoding))
            return None

        self.__log.return_(data)
        return data

    def _prepare_gn_queries(self, xml):
        """Create a request object with authentication.

        :param str xml:
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

        :param xml.etree.ElementTree.Element gn_queries:
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

        headers = {
            "Connection": "keep-alive" if http_keep_alive else "close",
        }
        self._conx.request(
            "POST", self.API_PATH, body=gn_queries_bytes, headers=headers)
        response = self._conx.getresponse()
        response_bytes = response.read()
        response.close()
        self.__log.debug(
            "%d %s\n%s\nresponse_bytes = %r", response.status, response.reason,
            response.headers, response_bytes)

        if not http_keep_alive:
            self._conx.close()

        if response.status != 200:
            cmd = gn_queries.find("QUERY").get("CMD")
            raise MetadataError(
                "HTTP %d %s" % (response.status, response.reason),
                context_hint="Gracenote %s" % cmd)

        gn_responses = ET.fromstring(response_bytes.decode("UTF-8"))
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
class MusicBrainzMetadataCollector(MetadataCollector):
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

        :param flacmanager.TOC toc: a disc's table of contents
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
        :param flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)
        super().__init__(toc)

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

        self.timeout = config.getfloat("HTTP", "timeout")
        self._conx = HTTPConnection(self.API_HOST, timeout=self.timeout)

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

        album_metadata = self.album
        tracks_metadata = self.tracks
        for mb_release in mb_release_list.findall(
                "mb:release", namespaces=nsmap):
            # ElementTree does not use QNames for attributes in the default
            # namespace. This is fortunate, albeit incorrect, because there's
            # no way to pass the namespaces map to get(), and subbing in the
            # default namespace URI for every attribute get would be a PITA.
            release_mbid = mb_release.get("id")
            self.__log.info("processing release %r", release_mbid)

            title = mb_release.find("mb:title", namespaces=nsmap).text
            if title not in album_metadata["title"]:
                album_metadata["title"].append(title)

            mb_name = mb_release.find(
                "mb:artist-credit/mb:name-credit/mb:artist/mb:name",
                 namespaces=nsmap)
            if (mb_name is not None and
                    mb_name.text not in album_metadata["artist"]):
                album_artist = mb_name.text
                album_metadata["artist"].append(album_artist)
            else:
                album_artist = None

            mb_date = mb_release.find("mb:date", namespaces=nsmap)
            if mb_date is not None:
                year = mb_date.text.split('-', 1)[0]
                if len(year) == 4 and year not in album_metadata["year"]:
                    album_metadata["year"].append(year)

            #NOTE: MusicBrainz does not support genre information.

            cover_art_front = mb_release.find(
                "mb:cover-art-archive/mb:front", namespaces=nsmap).text
            if cover_art_front == "true":
                cover_art = self._get_cover_image(release_mbid)
                if cover_art and cover_art not in album_metadata["cover"]:
                    album_metadata["cover"].append(cover_art)

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
                album_metadata["disc_total"] = medium_count
                disc_path = \
                    "mb:medium/mb:disc-list/mb:disc[@id='%s']" % disc_id
                mb_disc = mb_medium_list.find(disc_path, namespaces=nsmap)
                if mb_disc is not None:
                    mb_position = mb_medium_list.find(
                        disc_path + "/../../mb:position", namespaces=nsmap)
                    album_metadata["disc_number"] = int(mb_position.text)
                    mb_track_list = mb_medium_list.find(
                            disc_path + "/../../mb:track-list",
                            namespaces=nsmap)

            if mb_track_list is None:
                self.__log.warning(
                    "unable to find a suitable track list for release %r",
                    release_mbid)
                continue

            track_count = int(mb_track_list.get("count"))
            if track_count != len(self.toc.track_offsets):
                self.__log.warning(
                    "skipping track list (expected %d tracks, found %d)",
                    len(self.toc.track_offsets), track_count)

            for mb_track in mb_track_list.findall(
                    "mb:track", namespaces=nsmap):
                track_number = int(
                    mb_track.find("mb:number", namespaces=nsmap).text)
                track_metadata = tracks_metadata[track_number]

                title = mb_track.find(
                    "mb:recording/mb:title", namespaces=nsmap).text
                if title not in track_metadata["title"]:
                    track_metadata["title"].append(title)

                mb_name = mb_track.find(
                    "mb:recording/mb:artist-credit/mb:name-credit/mb:artist/"
                        "mb:name",
                    namespaces=nsmap)
                # MusicBrainz doesn't suppress the artist name even if it's the
                # same as the release's artist name
                if (mb_name is not None and
                        mb_name.text != album_artist and
                        mb_name.text not in track_metadata["artist"]):
                    track_metadata["artist"].append(mb_name.text)

                #NOTE: MusicBrainz does not support genre information.

        album_metadata["performer"] = list(album_metadata["artist"])
        for track_metadata in tracks_metadata[1:]:
            track_metadata["performer"] = list(track_metadata["artist"])

    def _prepare_discid_request(self, disc_id):
        """Build a full MusicBrainz '/discid' request path.

        :param str disc_id: a MusicBrainz Disc ID
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

        :param str request_path: a MusicBrainz HTTP request path
        :param dict nsmap: namespace prefixes to URIs
        :keyword bool http_keep_alive:
           whether or not to keep the MusicBrainz HTTP connection alive
        :return: the MusicBrainz <metadata> response document
        :rtype: :class:`xml.etree.ElementTree.Element`

        If this method returns, then /metadata is guaranteed to exist;
        otherwise, a ``MetadataError`` with an appropriate message is
        rasied.

        """
        self.__log.call(request_path, nsmap, http_keep_alive=http_keep_alive)

        headers = {
            "User-Agent": self.user_agent,
            "Connection": "keep-alive" if http_keep_alive else "close",
        }
        self._conx.request("GET", request_path, headers=headers)
        response = self._conx.getresponse()
        response_bytes = response.read()
        response.close()
        self.__log.debug(
            "%d %s\n%s\nresponse_bytes = %r", response.status, response.reason,
            response.headers, response_bytes)

        if not http_keep_alive:
            self._conx.close()

        if response.status != 200:
            raise MetadataError(
                "HTTP %d %s" % (response.status, response.reason),
                context_hint="MusicBrainz API")

        if "Content-Type" in response.headers:
            (_, params) = cgi.parse_header(response.headers["Content-Type"])
            encoding = params.get("charset", "UTF-8")
        else:
            encoding = "UTF-8"

        mb_response = ET.fromstring(response_bytes.decode(encoding))
        if (mb_response.tag == "error" or
                mb_response.tag != "{%s}metadata" % nsmap["mb"]):
            mb_text = mb_response.find("text")
            if mb_text is not None:
                message = mb_text.text
            else:
                message = \
                    "Unexpected response root element <%s>" % mb_response.tag
            raise MetadataError(message, context_hint="MusicBrainz API")

        self.__log.return_(mb_response)
        return mb_response

    def _get_cover_image(self, release_mbid):
        """Download a cover image.

        :param str release_mbid: a MusicBrainz release ID
        :return: raw image :obj:`bytes` data

        """
        self.__log.call(release_mbid)

        url = urlparse(self.COVERART_URL_TEMPLATE % release_mbid)
        host = url.netloc
        path = url.path if not url.query else "%s?%s" % (url.path, url.query)
        headers={"User-Agent": self.user_agent}

        conx = HTTPConnection(host, timeout=self.timeout)
        conx.request("GET", path, headers=headers)
        response = conx.getresponse()
        while response.status in [301, 302, 307]:
            response.close()
            url = urlparse(response.headers["Location"])
            if url.netloc != host:
                conx.close()
                conx = HTTPConnection(url.netloc, timeout=self.timeout)
            path = (url.path if not url.query
                else "%s?%s" % (url.path, url.query))
            conx.request("GET", path, headers=headers)
            response = conx.getresponse()

        data = response.read()
        response.close()
        conx.close()

        if response.status != 200:
            if "Content-Type" in response.headers:
                (_, params) = cgi.parse_header(
                    response.headers["Content-Type"])
                encoding = params.get("charset", "UTF-8")
            else:
                encoding = "UTF-8"
            self.__log.warning(
                "unable to get cover art for mbid %r (HTTP %d %s: %s)",
                release_mbid, response.status, response.reason,
                data.decode(encoding))
            return None

        self.__log.return_(data)
        return data


@logged
class MetadataPersistence(MetadataCollector):
    """A pseudo-client that populates **persisted** album and track
    metadata choices for a disc.

    """

    def __init__(self, toc):
        """
        :param flacmanager.TOC toc: a disc's table of contents

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
        self.restored = False

    def collect(self):
        """Populate metadata choices from persisted data."""
        self.__log.call()

        super().collect()

        if os.path.isfile(self.metadata_path):
            self.__log.debug("found %r", self.metadata_path)
            with open(self.metadata_path) as fp:
                disc_metadata = json.load(fp, object_pairs_hook=OrderedDict)

            self._postprocess(disc_metadata)

            self.album = disc_metadata["album"]
            self.tracks = disc_metadata["tracks"]
            self.restored = True

            self.__log.info(
                "restored metadata for DiscId %s from %s",
                self.disc_id, disc_metadata["timestamp"])
        else:
            self.__log.info("did not find %r", self.metadata_path)

    def _postprocess(self, disc_metadata):
        """Modify *metadata* in place after deserializing from JSON.

        :param dict disc_metadata: the metadata for a disc

        """
        # convert album cover to byte string (raw image data) by encoding
        # the string to "Latin-1"
        # (see comment in the _convert_to_json_serializable(obj) method)
        for i in range(len(disc_metadata["album"]["cover"])):
            disc_metadata["album"]["cover"][i] = \
                disc_metadata["album"]["cover"][i].encode("Latin-1")

        if "record_label" not in disc_metadata["album"]: # new in 0.8.0
            disc_metadata["album"]["record_label"] = ""

        self._xform_custom_keys(literal_eval, disc_metadata["album"])

        for i in range(1, len(disc_metadata["tracks"])):
            self._xform_custom_keys(literal_eval, disc_metadata["tracks"][i])

    def store(self, metadata):
        """Persist a disc's metadata field values.

        :param dict metadata: the finalized metadata for a disc

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
        ordered_metadata["timestamp"] = datetime.datetime.now().isoformat()
        ordered_metadata["__version__"] = __version__
        ordered_metadata["TOC"] = self.toc
        ordered_metadata["album"] = metadata["album"]
        ordered_metadata["tracks"] = metadata["tracks"]

        self._preprocess(ordered_metadata)

        with open(self.metadata_path, 'w') as fp:
            json.dump(
                ordered_metadata, fp, separators=(',', ':'),
                default=self._convert_to_json_serializable)

        self.__log.info("wrote %s", self.metadata_path)

    def _preprocess(self, disc_metadata):
        """Modify *metadata* in place before serializing as JSON.

        :param dict disc_metadata: the metadata for a disc

        """
        self._xform_custom_keys(repr, disc_metadata["album"])

        for i in range(1, len(disc_metadata["tracks"])):
            self._xform_custom_keys(repr, disc_metadata["tracks"][i])

    def _xform_custom_keys(self, func, metadata):
        """Convert ``key`` to ``func(key)`` for each key in
        *metadata["__custom"]*.

        :param func: the key conversion function
        :param dict metadata: an album or track metadata mapping

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

        :param obj:
           an object to be converted into a serializable JSON value
        :return: a JSON-serializable representation of *obj*
        :rtype: :obj:`str`

        """
        if type(obj) is bytes:
            # JSON does not directly support binary data, so instead use the
            # Latin-1-decoded value, which will be properly converted to use
            # Unicode escape sequences by the json library.
            # (Unicode code points 0-255 are identical to the Latin-1 values.)
            return obj.decode("latin-1")
        else:
            raise TypeError("%r is not JSON serializable" % obj)


#: Used to pass data between a :class:`MetadataAggregator` thread and the main
#: thread.
_AGGREGATOR_QUEUE = queue.Queue(1)


@logged
class MetadataAggregator(MetadataCollector, threading.Thread):
    """The thread that aggregates metadata from multiple sources."""

    def __init__(self, toc):
        """
        :param flacmanager.TOC toc: a disc's table of contents

        """
        self.__log.call(toc)

        threading.Thread.__init__(self, daemon=True)
        MetadataCollector.__init__(self, toc)

        self.persistence = MetadataPersistence(toc)
        self._collectors = [
            self.persistence, # must be first
            GracenoteCDDBMetadataCollector(toc),
            MusicBrainzMetadataCollector(toc),
        ]
        self.exception = None

    def run(self):
        """Run the :meth:`collect` method in another thread."""
        self.__log.call()

        try:
            self.collect()
        except Exception as e:
            self.__log.error("aggregation error: %r", e)
            self.exception = e
        self.__log.info("enqueueing %r", self)
        _AGGREGATOR_QUEUE.put(self)

    def collect(self):
        """Populate metadata from all music databases."""
        self.__log.call()

        MetadataCollector.collect(self)

        try:
            for collector in self._collectors:
                collector.collect()

                self._merge_metadata(
                    ["title", "artist", "performer", "record_label", "year",
                        "genre", "cover"],
                    collector.album, self.album)

                for field in ["disc_number", "disc_total"]:
                    if collector.album[field] > self.album[field]:
                        self.album[field] = collector.album[field]

                track_ordinal = 1
                for track_metadata in collector.tracks[1:]:
                    self._merge_metadata(
                        ["title", "artist", "performer", "year", "genre"],
                        track_metadata, self.tracks[track_ordinal])
                    track_ordinal += 1
        finally:
            # persisted metadata takes precedence
            if self.persistence.restored:
                self.album["disc_number"] = \
                    self.persistence.album["disc_number"]
                self.album["disc_total"] = self.persistence.album["disc_total"]
                # persisted data stores the "is_compilation" flag for albums
                self.album["is_compilation"] = \
                    self.persistence.album["is_compilation"]
                # custom tagging data for the album
                if "__custom" in self.persistence.album:
                    self.album["__custom"] = self.persistence.album["__custom"]
                # persisted data stores the "include" flag and "__custom"
                # tagging data for tracks (regular collectors do not)
                track_ordinal = 1
                for track_metadata in self.persistence.tracks[1:]:
                    self.tracks[track_ordinal]["include"] = \
                        self.persistence.tracks[track_ordinal]["include"]
                    if "__custom" in self.persistence.tracks[track_ordinal]:
                        self.tracks[track_ordinal]["__custom"] = \
                            self.persistence.tracks[track_ordinal]["__custom"]
                    track_ordinal += 1

    def _merge_metadata(self, fields, source, target):
        """Merge *source[field]* values into *target[field]*.

        :param list fields: metadata field names
        :param dict source: metadata being merged from
        :param dict target: metadata being merged into

        """
        self.__log.call(fields, source, target)

        for field in fields:
            for value in source[field]:
                if value not in target[field]:
                    target[field].append(value)


def get_lame_genres():
    """Return the list of genres recognized by LAME."""
    _log.call()

    # simple memo
    genres = getattr(get_lame_genres, "_fm_cached_genres", None)
    if genres is None:
        genres = []
        # why does lame write the genre list to stderr!? That's lame (LOL)
        output = subprocess.check_output(
            ["lame", "--genre-list"], stderr=subprocess.STDOUT)
        for genre in StringIO(output.decode(sys.getfilesystemencoding())):
            (genre_number, genre_label) = genre.strip().split(None, 1)
            genres.append(genre_label)
        get_lame_genres._fm_cached_genres = sorted(genres)

    # always return a copy so that the list can be modified without changing
    # the cached value
    _log.return_(genres)
    return list(genres)


def show_exception_dialog(e, aborting=False):
    """Open a dialog to display exception information.

    :param Exception e: a caught exception
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
            "\n\nWARNING! %s will abort after this message is dismissed!" %
                FLACManager.TITLE)

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

    root = tk.Tk()
    root.title(FLACManager.TITLE)
    root.minsize(1024, 768)

    try:
        FLACManager(master=root).mainloop()
    except Exception as e:
        _log.exception("aborting")
        show_exception_dialog(e, aborting=True)
        print("%s: %s" % (e.__class__.__name__, e), file=sys.stderr)
        sys.exit(1)

    sys.exit(0)

