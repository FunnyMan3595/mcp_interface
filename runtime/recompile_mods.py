#!/usr/bin/env python
# mcp_rebuild - A Python script for safe and easy rebuilding of MCP projects.
# Copyright (c) 2011 FunnyMan3595 (Charlie Nolan)
# This code is made avilable under the MIT license.  See LICENSE for the full
# details.

import itertools, os, os.path, platform, shutil, subprocess, sys, tarfile, \
       zipfile, tempfile, fnmatch, re, collections, StringIO, contextlib, \
       traceback

from patch import fromfile as build_patch

CONF_TOKEN = re.compile("%conf:([^%]*)%")

class KnownFailure(Exception):
    pass

class CompileFailed(KnownFailure):
    pass

class ObfuscateFailed(KnownFailure):
    pass

# Convenience functions.  These make the settings settings easier to work with.
absolute = lambda rawpath: os.path.abspath(os.path.expanduser(rawpath))
relative = lambda relpath: absolute(os.path.join(BASE, relpath))
def make_if_needed(dir):
    if not os.path.exists(dir):
        os.makedirs(dir)
def create_or_clean(dir):
    if os.path.exists(dir):
        shutil.rmtree(dir)
    os.makedirs(dir)

CLIENT, SERVER, FORGE = range(3)

BASE = absolute(".")
USER = relative("mods")
TEMP = relative("temp/mods")
LIB = relative("lib")
MCP_TEMP = relative("temp")
TARGET = relative("packages")

MCP_SRC = [relative("src/minecraft"),
           relative("src/minecraft_server"),
           relative("src/minecraft")]

# Most of this script assumes it's in the MCP directory, so let's go there.
os.chdir(BASE)

# Create the project directory and force it to be seen as a category.
if not os.path.exists(USER):
    os.makedirs(USER)

    # Touch the CATEGORY file.
    with open(os.path.join(USER, "CATEGORY"), "w") as catfile:
        catfile.write("This is a placeholder file to mark this directory as a "
                      "category, not a project.")

# Create/clean the temp directory.
create_or_clean(TEMP)

# Create/clean the package directory.
create_or_clean(TARGET)

# JAR files to build against.
DEOBF_CLIENT = relative("temp/minecraft_exc.jar")
DEOBF_SERVER = relative("temp/minecraft_server_exc.jar")

# MCP's bin directory, the directory MCP will obfuscate from.
MCP_BIN = relative("bin")
# The obvious subdirectories.
MCP_BIN_CLIENT = os.path.join(MCP_BIN, "minecraft")
MCP_BIN_SERVER = os.path.join(MCP_BIN, "minecraft_server")

# MCP's reobf directory, the directory MCP will place reobfuscated classes in.
MCP_REOBF = relative("reobf")
# The obvious subdirectories.
MCP_REOBF_CLIENT = os.path.join(MCP_REOBF, "minecraft")
MCP_REOBF_SERVER = os.path.join(MCP_REOBF, "minecraft_server")

# This class is used to represent a user project, also known as a subdirectory
# of USER.  The format is described in the README.
class Project(object):
    def __init__(self, directory):
        self.dir = directory

        self.disabled = (self.get_config("DISABLE", data_type=bool)
                         or self.get_config("DISABLED", data_type=bool))

        if self.disabled:
            return

        self.name            = self.get_config("PROJECT_NAME", os.path.basename(directory))
        self.version         = self.get_config("VERSION",      "alpha")
        self.package_name    = self.get_config("PACKAGE_NAME", self.name + "-" + self.version)
        self.extension       = self.get_config("EXTENSION",    "zip")
        self.dependencies    = self.get_config("DEPENDENCIES", [], data_type=list)
        self.api             = self.get_config("API",          [], data_type=list)
        self.hide_source     = self.get_config("HIDE_SOURCE",  False, data_type=bool)
        self.suppress_warnings     = self.get_config("NOT_MY_CODE",  False, data_type=bool)

    def get_config(self, setting, default=None, data_type=str):
        filename = os.path.join(self.dir, "conf", setting)
        exists = os.path.isfile(filename)

        if data_type == bool:
            return exists
        elif not exists:
            return default
        else:
            contents = open(filename).read().strip()
            if data_type == str:
                return contents
            elif data_type == list:
                contents = contents.replace(",","\n").replace(";","\n")
                return contents.split()


    @staticmethod
    def collect_projects(root, projects):
        """Collects all the active projects under root into projects."""
        for (dir, subdirs, files) in os.walk(root, followlinks=True):
            if "DISABLED" in files or "DISABLE" in files:
                # This project or category has been disabled.  Skip it.
                del subdirs[:]
                print "Disabled project or category at %s." % dir
            elif "CATEGORY" in files:
                # This is a category, not a project.  Continue normally.
                pass
                print "Found category at %s, recursing." % dir
            else:
                # This is a project.  Create it, but do not continue into
                # subdirectories.
                project = Project(dir)
                if project.disabled:
                    print "Disabled project or category at %s.  (Disabled by conf/)" % dir
                else:
                    projects.append(Project(dir))
                    print "Found project at %s." % dir

                del subdirs[:]

    def copy_files(self, source, dest, failcode):
        for (source_dir, subdirs, files) in os.walk(source, followlinks=True):
            dest_dir = os.path.join(dest, os.path.relpath(source_dir, source))
            make_if_needed(dest_dir)

            for file in files:
                if file.startswith("."):
                    continue

                try:
                    shutil.copy2(os.path.join(source_dir, file), dest_dir)
                except shutil.WindowsError:
                    pass # Windows doesn't like copying access time.

            for i in range(len(subdirs), 0, -1):
                if subdirs[i-1].startswith("."):
                    del subdirs[i-1]

    def get_package_file(self, side):
        if self.package_name is not None:
            filename = self.package_name
        else:
            if self.version is not None:
                filename = "%s-%s" % (self.name, self.version)
            else:
                filename = "%s" % self.name

        if side == SERVER:
            filename += "-server"
        elif side == FORGE:
            filename += "-universal"

        filename += "." + self.extension

        return os.path.join(TEMP, filename)

    @staticmethod
    def collect_files(root, relative=False, required_extension=None):
        all_files = set()
        if not os.path.isdir(root):
            return all_files

        for (dir, subdirs, files) in os.walk(root, followlinks=True):
            for file in files:
                if file.startswith("."):
                    continue

                ext = os.path.splitext(file)[1].lower()
                if required_extension and ext != required_extension:
                    continue

                full_name = os.path.join(dir, file)
                if relative:
                    all_files.add(os.path.relpath(full_name, root))
                else:
                    all_files.add(full_name)

            for i in range(len(subdirs), 0, -1):
                if subdirs[i-1].startswith("."):
                    del subdirs[i-1]

        return all_files

    def zip(self, archive_name, files=None, clean=False, do_replace=False):
        if clean or not os.path.exists(archive_name):
            mode = "w"
        else:
            mode = "a"

        archive = zipfile.ZipFile(archive_name, mode)
        try:
            if files is None:
                for dir, subdirs, files in os.walk(".", followlinks=True):
                    for file in files:
                        full_path = os.path.join(dir, file)
                        if do_replace:
                            contents = self.replace_conf(full_path)
                            full_path = os.path.relpath(full_path)
                            archive.writestr(full_path, contents)
                        else:
                            archive.write(full_path)
            else:
                for file in files:
                    if do_replace:
                        contents = self.replace_conf(file)
                        full_path = os.path.relpath(full_path)
                        archive.writestr(file, contents)
                    else:
                        archive.write(file)
        finally:
            archive.close()

    def get_source_dirs(self, side):
        source_dirs = [os.path.join(self.dir, "src", "common")]
        if side == CLIENT:
            source_dirs.append(os.path.join(self.dir, "src", "client"))
        elif side == SERVER:
            source_dirs.append(os.path.join(self.dir, "src", "server"))

        return source_dirs

    def shorten_filename(self, filename):
        path = [os.path.relpath(filename, self.dir)]

        while path[0] != '':
            path[0:1] = os.path.split(path[0])

        if len(path) < 4:
            return None

        return os.path.join(*path[3:])

    def is_api(self, filename):
        short_filename = self.shorten_filename(filename)
        if short_filename is None:
            return False

        for entry in self.api:
            if fnmatch.fnmatch(short_filename, entry):
                return True

        return False

    def replace_conf(self, filename, output_root=None):
        input_name = filename
        output_name_raw = self.shorten_filename(filename)

        split = CONF_TOKEN.split(output_name_raw)
        output_name = ""
        for index, token in enumerate(split):
            if index % 2 == 0:
                output_name += token
            else:
                replacement = self.get_config(token)
                if replacement is not None:
                    output_name += replacement
                elif token == "PROJECT_NAME":
                    output_name += self.name
                else:
                    raise CompileFailed("No conf token '%s'" % token)

        if output_root is not None:
            output = os.path.join(output_root, output_name)

            outdir = os.path.dirname(output)
            if not os.path.exists(outdir):
                os.makedirs(outdir)

            stream = open(output, "w")
        else:
            output = None

            @contextlib.contextmanager
            def string_stream():
                yield StringIO.StringIO()

            stream = string_stream()

        with open(input_name) as infile:
            contents = infile.read()

        split = CONF_TOKEN.split(contents)

        with stream as outfile:
            for index, token in enumerate(split):
                if index % 2 == 0:
                    outfile.write(token)
                else:
                    replacement = self.get_config(token)
                    if replacement is not None:
                        outfile.write(replacement)
                    elif token == "PROJECT_NAME":
                        outfile.write(self.name)
                    else:
                        raise CompileFailed("No conf token '%s'" % token)

        if output is None:
            return outfile.getvalue()
        return output

    def apply_patch(self, patch_file, output_root, side):
        patchset = build_patch(patch_file)
        if not patchset:
            raise CompileFailed("Malformed patch: %s" % patch_file)

        patch_name = self.shorten_filename(patch_file)
        # Store the target file for single-file patches.
        target_file = None
        if ".java" in patch_name.lower():
            # Strip everything after .java.
            target_file = patch_name[:patch_name.lower().index(".java") + 5]

        src = MCP_SRC[side]
        fallback_src = None
        if side == FORGE:
            fallback_src = MCP_SRC[CLIENT]

        patched_files = set()
        for patch in patchset.items:
            # Try to figure out which file we're patching.
            patch_target = None
            for possible_target in [patch.source, patch.target, target_file]:
                if possible_target is None:
                    continue
                possible_target = os.path.normpath(possible_target)

                # Collect the locations where this possibility might be.
                source_files = [os.path.join(src, possible_target)]
                if fallback_src is not None:
                    source_files.append(os.path.join(fallback_src,
                                                     possible_target))

                for source_file in source_files:
                    if os.path.exists(source_file):
                        # If it's actually here, use it.
                        patch_target = possible_target
                        target_location = os.path.join(output_root,
                                                       patch_target)

                        os.makedirs(os.path.dirname(target_location))
                        shutil.copy2(source_file, target_location)

                if patch_target is not None:
                    break
                else:
                    add_warning(self, side, "Bad file specified in patch:\n    Patch file: %s\n    Target file: %s" % (patch_file, possible_target))

            if patch_target is None:
                raise CompileFailed("Multi-file patch did not specify a file to patch!\n    Patch file: %s" % (patch_file))
            elif target_file is not None and patch_target != target_file:
                raise CompileFailed("Single-file patch mismatch:\n    Patch file: %s\n    Target file: %s\n    Attempted to patch: %s" % (patch_file, target_file, patch_target))

            # Set both properties to the target file, since we know exactly
            # what we're patching.
            patch.source = patch_target
            patch.target = patch_target

            patched_files.add(target_location)

        if not patchset.apply(root=output_root):
            raise CompileFailed("Patch failed to apply: %s" % (patch_file))

        return patched_files

    def compile(self, all_projects, side, out_dir, temp_dir, library_classpath, api=False):
        create_or_clean(temp_dir)

        source_files = set()
        patch_files = set()
        for dir in self.get_source_dirs(side):
            source_files.update(self.collect_files(dir, required_extension=".java"))
            patch_files.update(self.collect_files(dir, required_extension=".diff"))
            patch_files.update(self.collect_files(dir, required_extension=".patch"))

        if api:
            source_files = filter(self.is_api, source_files)

        source_files = map(lambda f: self.replace_conf(f, temp_dir),
                           source_files)
        for patch in patch_files:
            for patched_file in self.apply_patch(patch, temp_dir, side):
                source_files.append(patched_file)

        source_dirs = [temp_dir]
        for dep in self.dependencies:
            project = all_projects.get(dep, None)
            if project is None:
                add_warning(self, side, "Depends on %s, which is not available!" % dep)
                continue
            source_dirs += project.get_source_dirs(side)

        if side in [CLIENT, FORGE]:
            classpath = MCP_BIN_CLIENT + ":" + library_classpath
        else: # if side == SERVER:
            classpath = MCP_BIN_SERVER + ":" + library_classpath

        if self.suppress_warnings:
            command = ["javac",
                       "-sourcepath", ":".join(source_dirs), "-classpath",
                       classpath, "-d", out_dir] + list(source_files)
        else:
            command = ["javac", "-Xlint:all",
                       "-sourcepath", ":".join(source_dirs), "-classpath",
                       classpath, "-d", out_dir] + list(source_files)

        self.call_or_die(command, CompileFailed)

    def obfuscate(self, side, stored_inheritance):
        classpath = "runtime/bin/jcommander-1.29.jar:lib/asm-all-4.0.jar:runtime/bin/mcp_deobfuscate-1.2.jar"
        main_class = "org.ldg.mcpd.MCPDeobfuscate"
        outdir = TARGET

        if side in [CLIENT, FORGE]:
            config = os.path.join(MCP_TEMP, "client_ro.srg")
            mc_jar = DEOBF_CLIENT
        else: #if side == SERVER:
            config = os.path.join(MCP_TEMP, "server_ro.srg")
            mc_jar = DEOBF_SERVER

        command = ["java", "-classpath", classpath, main_class,
                   "--stored_inheritance"] +  stored_inheritance + ["--invert",
                   "--config", config, "--outdir", outdir, "--indir", "/",
                   "--infiles", self.get_package_file(side)]

        print "---Obfuscating %s---" % self.name
        self.call_or_die(command, ObfuscateFailed)
        print "---Obfuscation complete---"
        print

    def call_or_die(self, cmd, error, shell=False):
        exit = subprocess.call(cmd, shell=shell)
        if exit != 0:
            if shell:
                raise error("Command failed: %s" % cmd)
            else:
                raise error("Command failed: %s" % cmd[0])

    def package(self, side, in_dir):
        """Packages this project's files."""
        created = False
        package = self.get_package_file(side)
        if os.path.exists(package):
            # Ensure a clean start.  Should already be done by now, though.
            os.remove(package)

        # Side-specific directories
        if side == CLIENT:
            source = os.path.join(self.dir, "src", "client")
            resources = os.path.join(self.dir, "resources", "client")
        elif side == SERVER:
            source = os.path.join(self.dir, "src", "server")
            resources = os.path.join(self.dir, "resources", "server")

        if not self.hide_source:
            ## Collect and package source files.
            # Common first, so they can be overridden.
            common_source = os.path.join(self.dir, "src", "common")
            if os.path.isdir(common_source) and os.listdir(common_source):
                # To package these, we just change to the appropriate directory
                # and let self.zip find everything in it.
                os.chdir(common_source)
                self.zip(package)
                created = True


            if side != FORGE and os.path.isdir(source) and os.listdir(source):
                os.chdir(source)
                self.zip(package)
                created = True

        ## Collect and package class files.
        if os.path.exists(in_dir) and os.listdir(in_dir):
            os.chdir(in_dir)
            self.zip(package)
            created = True


        ## Collect and package resource files.
        # Common first, so they can be overridden.
        common_resources = os.path.join(self.dir, "resources", "common")
        if os.path.isdir(common_resources):
            # To package these, we just change to the appropriate directory
            # and let the shell and zip command find everything in it.
            os.chdir(common_resources)
            self.zip(package, do_replace=True)
            created = True

        if side != FORGE and os.path.isdir(resources):
            os.chdir(resources)
            self.zip(package, do_replace=True)
            created = True

        os.chdir(BASE)
        return created

FORGE_INSTALLED = False
with open(relative("runtime/commands.py")) as source:
    contents = source.read()
    if "FML" in contents:
        FORGE_INSTALLED = True

if FORGE_INSTALLED:
    print "!!! Forge detected.  Building universal packages only. !!!"
    print

projects = []
if not os.path.isdir(USER):
    print "No user directory found.  Nothing to do."
    sys.exit(0)
else:
    Project.collect_projects(USER, projects)

if os.path.exists(os.path.join(LIB, "client_reobf.jar.inh")) \
   or os.path.exists(os.path.join(LIB, "server_reobf.jar.inh")):
    pass # Yay!
else:
    print "Please run deobfuscate_libs first."
    sys.exit(1)

libraries = []
stored_inheritance = []
for filename in os.listdir(LIB):
    base, extension = os.path.splitext(filename)
    if extension.lower() == ".inh":
        stored_inheritance.append(os.path.join(LIB, filename))
    elif extension.lower() in [".jar", ".zip"]:
        libraries.append(os.path.join(LIB, filename))

library_classpath = ":".join(libraries)

projects_dict = {}
for project in projects:
    projects_dict[project.name] = project

if FORGE_INSTALLED:
    sides = [FORGE]
else:
    sides = [CLIENT, SERVER]

warnings = collections.defaultdict(lambda: [])
def add_warning(project, side, warning):
    warnings[project].append((side, warning))

errors = collections.defaultdict(lambda: [])
def add_error(project, side, error):
    errors[project].append((side, error, sys.exc_info()[2]))

compile_temp = os.path.join(TEMP, "compile_temp")

api_dir = os.path.join(TEMP, "lib")
create_or_clean(api_dir)
api_count = 0
for project in projects:
    if project.api:
        for side in sides:
            project.compile(projects_dict, side, api_dir, compile_temp, library_classpath, api=True)
            api_count += 1

print "Built %d APIs." % api_count

library_classpath += ":" + api_dir

count = 0
source_count = 0
client_count = 0
server_count = 0
for project in projects:
    if len(sys.argv) > 1 and not project.name in sys.argv:
        print "Skipping unrequested project %s." % project.name
        continue

    print "Processing %s..." % project.name
    any_created = False

    for side in sides:
        try:
            compile_dir = os.path.join(TEMP, project.name)
            if side == SERVER:
                compile_dir += "_server"
            elif side == FORGE:
                compile_dir += "_universal"

            create_or_clean(compile_dir)

            project.compile(projects_dict, side, compile_dir, compile_temp, library_classpath)

            created = project.package(side, compile_dir)

            if created:
                any_created = True
                project.obfuscate(side, stored_inheritance)

                if side == CLIENT:
                    client_count += 1
                elif side == SERVER:
                    server_count += 1
        except Exception, e:
            # You did something wrong!
            add_error(project, side, e)
    if any_created:
        count += 1
        if not project.hide_source:
            source_count += 1

s = "" if count == 1 else "s"
print "%d project%s compiled and packaged successfully." % (count, s)
if count and not FORGE_INSTALLED:
    print "(%d client, %d server)" % (client_count, server_count)
if count:
    if source_count == 0:
        smiley = ":/"
    elif source_count == count:
        smiley = ":D"
    else:
        smiley = ":)"

    print "Source included in packages for %d/%d projects.  %s" % (source_count, count, smiley)

def print_messages(project_messages):
    for project, messages in project_messages.items():
        for info in messages:
            side, message = info[:2]
            if side == CLIENT:
                side_name = "client"
            elif side == SERVER:
                side_name = "server"
            else: # if side == FORGE:
                side_name = "universal"
            print "%s (%s): %s" % (project.name, side_name, message)

            if len(info) == 3 and isinstance(message, Exception):
                if not isinstance(message, KnownFailure):
                    traceback.print_tb(info[2])


if warnings:
    print
    print "Warnings in %d projects:" % len(warnings)

    print_messages(warnings)

if errors:
    print
    print "Errors in %d projects:" % len(errors)

    print_messages(errors)

    sys.exit(len(errors))
