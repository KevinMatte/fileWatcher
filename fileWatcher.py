#!bin/venv/bin/python

"""
Watches a set of directories or files for any change and exits on change.

See full details in process_args() below. or running python filesWatcher.py -h

"""

import glob
import os.path
import time
import re
import yaml
import subprocess
from argparse import ArgumentParser, Namespace, RawTextHelpFormatter
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, DirCreatedEvent, FileCreatedEvent, DirDeletedEvent, \
    FileDeletedEvent, DirModifiedEvent, FileModifiedEvent, DirMovedEvent, FileMovedEvent

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper


def process_args() -> tuple[Namespace, ArgumentParser]:

    # Program's description
    help_program = """Monitor's a paths and executes commands when something changes:
    
        Example YAML:
# ==========================================================================
# This sets up default values for all following targets.
# The __defaults__ for one YAML file are NOT passed onto the next.
#
# Note: A list of one string in the YAML can be replaced with the one string for easier reading.
"__defaults__":
  # When the file in '--skip_file {file}' exists, these commands are run rather than the 'commands'
  skipped: "bin/say.py '_MONITOR_NAME_: Skip file exists. Skipping.' 2>/dev/null"

  # Executed before running 'commands'
  started: "bin/say.py '_MONITOR_NAME_: Starting' 2>/dev/null"

  # Executed after running 'commands' successfully.
  completed:
    - "make build_done"
    - "rm -f tmp/build.locked"
    - "bin/say.py '_MONITOR_NAME_: Completed' 2>/dev/null"

  # Executed after running 'commands' failed.
  error:
    - "rm -f tmp/build.locked"
    - "bin/say.py '_MONITOR_NAME_: Error' 2>/dev/null"

  # Fall-back commands if 'commands' is not specified.
  # Note: A single string is recognized as a short form of a list of one string.
  commands: "bin/say.py '_MONITOR_NAME_: Missing commands' 2>/dev/null"

# This is the start of a declaration of files/paths to monitor and commands to execute.
"static and environment":

  # When a file/path changes, these commands will be executed.
  commands:
    - "make update_dot_env"
    - "make -C images/demo_ui"
    - "make update_dot_env"
    - "make -C images/demo_ui build_static"

  # A list of files/paths and file name patterns to monitor.
  searches:
    # Not used. Just nice to see.
    - name: 'static'
      # A list of paths to directories or files.
      # Note: A single string is recognized as a short form of a list of one string.
      paths: "images/demo_ui/src/static"
      # Optional. If given, the full path must match this python regular expression.
      # You could include directories in these patterns, but if possible, don't, I think.
      patterns: ['.*\.js$', '.*\.jsx$', '.*\.css$', '.*\.htm$l', '.*\.py$', '.*\.php$']

    # A search example without patterns.
    - name: 'environment'
      paths: ['.env', '.secrets.env']

"demo_ui front":
  # Note: A single string is recognized as a short form of a list of one string.
  commands: "make -C images/demo_ui build_front"
  searches:
    - name: 'Front UI'
      paths: ["images/demo_ui/src/front"]
      patterns: ['.*\.jsx$', '.*\.css$', '.*\.html$']

# For reloading this YAML file.
# Note: Reloads happen every time changes are discovered.
"monitorBuild YAML Changed":
  commands: "bin/say.py  'Reloading monitorBuild YAML.' 2>/dev/null"
  searches: 'bin/monitorBuild.yaml'

# --------------------------------------------------------------------------
        """

    # Help on paths from program's argv
    help_args_paths = "One or more YAML files like the above."
    help_opt_verbose = "Print extra processing information to stdout."
    help_opt_repeat = "After targets found, repeat monitor in 1 second"
    help_skip_file = "If provided, when a file is changed and this file exists, the commands are skipped."

    # Parse command-line argument
    arg_parser = ArgumentParser(formatter_class=RawTextHelpFormatter, description=help_program)
    arg_parser.add_argument('paths', nargs="*", help=help_args_paths)
    arg_parser.add_argument("-r", "--repeat", help=help_opt_repeat, default=False, action='store_true')
    arg_parser.add_argument("-e", "--exit_on_error", help=help_opt_repeat, default=False, action='store_true')
    arg_parser.add_argument('-s', '--skip_file', default=None, help=help_skip_file)
    args = arg_parser.parse_args()

    if len(args.paths) == 0:
        arg_parser.print_help()
        exit(1)

    return args, arg_parser



class MonitorAnyFileChange(FileSystemEventHandler):
    """An event handler class for Observer instances.
    For the patch being monitored, keeps track of the 'commands', the file extensions and state of events.
    """

    def __init__(self, path, monitor_defn, monitor):
        super().__init__()

        # Store parameters
        self._path = path
        self.monitor_defn = monitor_defn
        self._monitor = monitor

        # Init
        self._files = set()

    def has_change(self) -> bool:
        """Returns True iff any file change event has occurred. """
        return len(self._files) > 0

    def get_files(self):
        return self._files

    def _handle_event(self, event):
        """Event handler for an Observer.

        If a matching any file extension or no extensions given for the target,
        records the event's filepath reference.
        """
        if 'patterns' in self._monitor:
            for file_extension in self._monitor['patterns']:
                if re.match(file_extension, event.src_path):
                    self._files.add(event.src_path)
                    break
        elif event.src_path:
            self._files.add(event.src_path)

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent) -> None:
        """@see _handle_event()"""
        self._handle_event(event)

    def on_deleted(self, event: DirDeletedEvent | FileDeletedEvent) -> None:
        """@see _handle_event()"""
        self._handle_event(event)

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        """@see _handle_event()"""
        self._handle_event(event)

    def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
        """@see _handle_event()"""
        self._handle_event(event)


class FilesWatcher:
    """Main class for this script. See help at top of file."""

    def __init__(self, args, parser):
        self.args = args
        self.parser = parser
        self.event_handlers = []
        self._monitor_defns_defaults = {}
        self.observer = None

    def _setup_observers(self):
        """Observers and event handlers are recreated on each call to start(). """
        self.event_handlers = []
        self.observer = Observer()

        # Parse monitor yamls and start them.
        print("Monitoring:")
        for yaml_file in self.args.paths:
            with open(yaml_file, 'r') as f:
                monitor_defns = yaml.load(f, Loader=Loader)
                self._start_monitors(yaml_file, monitor_defns)

        try:
            self.observer.start()
        except FileNotFoundError:
            if self.args.exit_on_error:
                exit(1)

    def _start_monitors(self, source, monitor_defns):
        self._monitor_defns_defaults = {}

        # Loop over array of monitor definitions.
        for defn_name, monitor_defn in monitor_defns.items():
            if defn_name == '__defaults__':
                self._monitor_defns_defaults = monitor_defn
                continue

            # At yaml key as a name and as a compined source filename and monitor name.
            monitor_defn = self._monitor_defns_defaults | monitor_defn
            monitor_defn['__name'] = defn_name
            monitor_defn['__key'] = f"{source}:{defn_name}"

            # Loop over search paths
            searches = monitor_defn['searches']
            if isinstance(searches, str):
                searches = [{"paths": [searches]}]
            elif isinstance(searches[0], str):
                searches = [{"paths": searches}]
            for search_defn in searches:
                found_path = False
                i_glob = None
                path_glob = None
                paths = search_defn['paths']
                if isinstance(paths, str):
                    paths = [paths]
                for i_glob, path_glob in enumerate(paths):
                    for path in glob.iglob(path_glob, recursive=True):
                        found_path = True
                        self._add_monitor(path, monitor_defn, search_defn)

                # Tree glob as a non-existent path if glob fails to expand.
                if not found_path and i_glob is not None:
                    print(f"ERROR: No glob expansion for: {path_glob}.")
                    if self.args.exit_on_error:
                        exit(1)

    def _add_monitor(self, path: str, monitor_defn, monitor):
        """Create and starts a new path Observer."""

        # Create Observer event handlers that contain extra context information.
        event_handler = MonitorAnyFileChange(path, monitor_defn, monitor)
        self.observer.schedule(event_handler, path, recursive=True)
        self.event_handlers.append(event_handler)

    def start(self):
        """
        :return: A set of target names where files have been changed (within 1 second of first change found)
        """
        self._setup_observers()

        # Monitor until a path change is observed.
        try:
            # 1 sec periodically check if there are any changes.
            has_change = False
            while not has_change:
                for event_handler in self.event_handlers:
                    if event_handler.has_change():
                        has_change = True
                        break

                time.sleep(1)
            
        finally:
            # Wait 1 more second for any other changes, before stopping observer.
            time.sleep(1)

            # Stop observer
            self.observer.stop()
            self.observer.join()

        # Keeping set of defn's run to avoid running twice.
        triggered_monitor_defn_keys = set()

        # Check if optional --skip-file {file} exists.
        has_skip_file = self.args.skip_file and os.path.exists(self.args.skip_file)

        # Iterate over event_handlers to find out which ones triggered.
        for event_handler in self.event_handlers:
            if event_handler.has_change():

                # Only execute commands for unrun monitor definitions.
                monitor_key = event_handler.monitor_defn['__key']
                if not monitor_key in triggered_monitor_defn_keys:
                    triggered_monitor_defn_keys.add(monitor_key)

                    if has_skip_file:
                        # Run 'skipped' commands
                        self._run_commands(event_handler.monitor_defn, 'skipped')
                    else:
                        # Run 'commands'
                        print(f"Executing {monitor_key}")
                        res = self._run_commands(event_handler.monitor_defn, 'commands')
                        self._run_commands(event_handler.monitor_defn, 'completed' if res == 0 else 'error')
                        print()

    def _run_commands(self, monitor_defn, commands_key):
        # If there is a list of commands to run
        if commands_key in monitor_defn:

            # Support commands as a single string, rather than a list of strings.
            commands = monitor_defn[commands_key]
            if isinstance(commands, str):
                commands = [commands]

            # Execute each command.
            for command in commands:
                command = command.replace('_MONITOR_NAME_', monitor_defn['__name'])
                res = subprocess.run(command, shell=True)
                if res.returncode:
                    # Return process error code.
                    return res.returncode

        # Return process success
        return 0


def main():
    """main function for direct run of script."""

    args, parser = process_args()
    files_watcher = FilesWatcher(args, parser)
    files_watcher.start()

    # If --repeat, re-initialize and start watch again.
    # Note: re-initializing may pick up globs that didn't previously exist.
    while args.repeat:
        time.sleep(1)
        print("---")
        files_watcher.start()


if __name__ == "__main__":
    main()
    exit(0)
