# FLACManager - audio metadata aggregator and FLAC+MP3 encoder

http://ninthtest.net/flac-mp3-audio-manager/

FLAC Manager is a plain [Tkinter](http://tkinter.unpythonic.net/wiki/) GUI
(i.e. no `tkinter.tix` or `tkinter.ttk`) that aggregates metadata for a CD-DA
disc from the [Gracenote Web API](https://developer.gracenote.com/web-api) and
the
[MusicBrainz XML Web Service](http://musicbrainz.org/doc/Development/XML_Web_Service/Version_2),
allows you to choose metadata values from the aggregated data (or enter
freeform values), and then rips tracks to FLAC and MP3. The encoding options
for FLAC and MP3 are fully configurable, and the directory/file naming scheme
is customizable.

Now, before you get all excited, understand that this application has several
oppressive limitations:

* only runs on Mac OS X
* has minimalistic UI and usability
* requires Python 3.3 or higher **with `tkinter` installed**
* requires the `flac` and `lame` command line utilities to be installed, as
  well as the `libdiscid` shared library
* requires that you register for *your own* authentication keys at
  https://developer.gracenote.com/

Read the warning disclaimer at http://ninthtest.net/flac-mp3-audio-manager/, as
well as the
[FLAC Manager prerequisites](http://ninthtest.net/flac-mp3-audio-manager/prerequisites.html)
to understand why these limitations exist.

## Download

Clone or fork the repository:

```bash
$ git clone https://github.com/mzipay/FLACManager.git
```

Alternatively, download and extract a source _.zip_ or _.tar.gz_ archive from
https://github.com/mzipay/FLACManager/releases.

There is no installation process; simply run `python flacmanager.py`.

