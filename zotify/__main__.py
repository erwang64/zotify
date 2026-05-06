#! /usr/bin/env python3

"""
Zotify
It's like youtube-dl, but for that other music platform.
"""

import argparse
import sys
from pathlib import Path

from zotify.app import client
from zotify.config import Zotify, CONFIG_VALUES, DEPRECIATED_CONFIGS
from zotify.termoutput import Printer


class DepreciatedAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        if "help" in kwargs:
            kwargs["help"] = "[DEPRECATED] " + kwargs["help"]
        super().__init__(option_strings, dest, **kwargs)
    
    def __call__(self, parser, namespace, values, option_string=None):
        Printer.depreciated_warning(option_string, self.help, CONFIG=False)
        setattr(namespace, self.dest, values)


DEPRECIATED_FLAGS = (
    {"flags":    ('-d', '--download',),     "type":    str,     "help":    'Use `--file` (`-f`) instead'},
)


def main():
    if "--gui" in sys.argv:
        try:
            from zotify.gui import launch_gui
        except ModuleNotFoundError as e:
            missing_module = getattr(e, "name", "")
            if missing_module == "customtkinter":
                print(
                    "GUI dependency missing: customtkinter.\n"
                    "Install it with:\n"
                    "  python -m pip install customtkinter\n"
                    "Then run again:\n"
                    "  python -m zotify --gui"
                )
                return
            raise
        launch_gui()
        return

    parser = argparse.ArgumentParser(prog='zotify',
        description='A music and podcast downloader needing only Python and FFMPEG.')
    
    parser.register('action', 'depreciated_ignore_warn', DepreciatedAction)
    
    # no args
    parser.add_argument('--version',
                        action='version',
                        version=f'Zotify {Zotify.VERSION}',
                        help='Show the version of Zotify')
    parser.add_argument('--persist',
                        action='store_true',
                        dest='persist',
                        help='Perform multiple queries with a single persistent Session')
    parser.add_argument('--update-config',
                        action='store_true',
                        dest='update_config',
                        help='Updates the `config.json` file while keeping all current settings unchanged')
    parser.add_argument('--update-archive',
                        action='store_true',
                        dest='update_archive',
                        help='Updates the `.song_archive` file entries with full paths while keeping non-findable entries unchanged')
    parser.add_argument('--debug',
                        action='store_true',
                        dest='debug',
                        help='Enable debug mode, prints extra information and creates a `config_DEBUG.json` file')
    parser.add_argument('--gui',
                        action='store_true',
                        dest='gui',
                        help='Launch the modern desktop GUI')
    parser.add_argument('--login-only',
                        action='store_true',
                        dest='login_only',
                        help='Authenticate and exit without running a download query')
    parser.add_argument('--logout',
                        action='store_true',
                        dest='logout',
                        help='Delete saved Spotify credentials and exit')
    parser.add_argument('-ns', '--no-splash',
                        action='store_true',
                        dest='no_splash',
                        help='Suppress the splash screen when loading')
    
    # with args
    parser.add_argument('-c', '--config', '--config-location',
                        type=str,
                        dest='config_location',
                        help='Specify a directory containing a Zotify `config.json` file to load settings')
    parser.add_argument('-u', '--username',
                        type=str,
                        dest='username',
                        help='Account username')
    parser.add_argument('--token',
                        type=str,
                        dest='token',
                        help='Authentication token')
    
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('urls',
                       type=str,
                       # action='extend',
                       nargs='*',
                       default="",
                       help='Download track(s), album(s), playlist(s), podcast episode(s), or artist(s) specified by the URL(s) passed as a command line argument(s). If an artist\'s URL is given, all albums by the specified artist will be downloaded. Can take multiple URLs as multiple arguments.')
    group.add_argument('-f', '--file',
                       type=str,
                       dest='file_of_urls',
                       help='Download all tracks/albums/episodes/playlists URLs within the file passed as argument')
    group.add_argument('-l', '--liked', '--liked-songs', 
                       action='store_true',
                       dest='liked_songs',
                       help='Download all Liked Songs on your account')
    group.add_argument('-p', '--playlist', '--playlists', '--user-playlists',
                       action='store_true',
                       dest='user_playlists',
                       help='Download playlist(s) created/saved by your account (interactive)')
    group.add_argument('-a', '--artist', '--artists', '--followed-artists',
                       action='store_true',
                       dest='followed_artists',
                       help='Download all songs by followed artist(s) (interactive)')
    group.add_argument('-m', '--album', '--albums', '--followed-albums',
                       action='store_true',
                       dest='followed_albums',
                       help='Download followed albums (interactive)')
    group.add_argument('-s', '--search',
                       type=str,
                       dest='search',
                       nargs='?',
                       const=' ',
                       help='Search tracks/albums/artists/playlists based on argument (interactive)')
    group.add_argument('-v', '--verify-library',
                       action='store_true',
                       dest='verify_library',
                       help='Check metadata for all tracks in ROOT_PATH or listed in SONG_ARCHIVE, updating the metadata if necessary. This will not download any new tracks, but may take a very, very long time.')
    modes = group._group_actions.copy()
    
    for flag in DEPRECIATED_FLAGS: 
        group.add_argument(*flag["flags"],
                           type=flag["type"],
                           help=flag["help"],
                           action='depreciated_ignore_warn')
    
    for key in DEPRECIATED_CONFIGS:
        parser.add_argument(*DEPRECIATED_CONFIGS[key]['arg'],
                            type=str,
                            action='depreciated_ignore_warn',
                            help=f'Delete the `{key}` flag from the commandline call'
                            )
    
    for key in CONFIG_VALUES:
        parser.add_argument(*CONFIG_VALUES[key]['arg'],
                            type=str, #type conversion occurs in config.parse_arg_value()
                            dest=key.lower(),
                            default=None,
                            )
    
    args = parser.parse_args()

    if args.logout:
        Zotify.CONFIG.load(args)
        cred_path = Path(Zotify.CONFIG.get_credentials_location())
        if cred_path.exists():
            cred_path.unlink()
            print(f"Credentials removed: {cred_path}")
        else:
            print(f"No credentials file found at: {cred_path}")
        return

    Zotify.boot(args)
    if args.login_only:
        print("Spotify login completed.")
        Zotify.end()
        return
    client(args, modes)


if __name__ == '__main__':
    main()
