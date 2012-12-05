#!/usr/bin/env python
# mcp_rebuild - A Python script for safe and easy rebuilding of MCP projects.
# Copyright (c) 2011 FunnyMan3595 (Charlie Nolan)
# This code is made avilable under the MIT license.  See LICENSE for the full
# details.

import itertools, os, os.path, platform, shutil, subprocess, sys, tarfile, \
       zipfile, tempfile

# Convenience functions.  These make the settings settings easier to work with.
absolute = lambda rawpath: os.path.abspath(os.path.expanduser(rawpath))
relative = lambda *relpath: absolute(os.path.join(BASE, *relpath))
def make_if_needed(dir):
    if not os.path.exists(dir):
        os.makedirs(dir)
def clean_if_needed(dir):
    if os.path.exists(dir):
        shutil.rmtree(dir)
    os.makedirs(dir)

BASE = absolute(".")
OBF_LIBS = relative("lib-obf")
DEOBF_LIBS = relative("lib")
TEMP = relative("temp/lib")
MCP_TEMP = relative("temp")

# Most of this script assumes it's in the MCP directory, so let's go there.
os.chdir(BASE)

# Create the project directory and force it to be seen as a category.
if not os.path.exists(OBF_LIBS):
    os.makedirs(OBF_LIBS)

# Create/clean the temp directory.
clean_if_needed(TEMP)

# JAR files to build against.
OBF_CLIENT = relative("temp/client_reobf.jar")
DEOBF_CLIENT = relative("temp/client_recomp.jar")
OBF_SERVER = relative("temp/server_reobf.jar")
DEOBF_SERVER = relative("temp/server_recomp.jar")

def call_or_die(cmd, shell=False):
    if shell:
        print "Running" + cmd
    else:
        print "Running " + (" ".join(cmd))
    exit = subprocess.call(cmd, shell=shell)
    if exit != 0:
        print "Command failed: %s" % cmd
        print "Aborting deobfuscate."
        sys.exit(1)

if os.path.exists(OBF_CLIENT) or os.path.exists(OBF_SERVER):
    pass # Yay!
else:
    print \
"""Please finish setting up MCP.  You must run decompile and reobfuscate before
deobfuscate_libs."""
    sys.exit(1)

FORGE_INSTALLED = relative("src", "common")
CLIENT_SRG = os.path.join(MCP_TEMP, "client_ro.srg")
SERVER_SRG = os.path.join(MCP_TEMP, "server_ro.srg")
SRG = os.path.join(TEMP, "full.srg")

if os.path.exists(FORGE_INSTALLED):
    shutil.copy2(CLIENT_SRG, SRG)
elif os.path.exists(CLIENT_SRG) and os.path.exists(SERVER_SRG):
    call_or_die("cat %s %s | sort -u > %s" % (CLIENT_SRG, SERVER_SRG, SRG),
                shell=True)
elif os.path.exists(CLIENT_SRG):
    shutil.copy2(CLIENT_SRG, SRG)
elif os.path.exists(SERVER_SRG):
    shutil.copy2(SERVER_SRG, SRG)
else:
    print "You must run reobfuscate before deobfuscate_libs."
    sys.exit(1)

CLASSPATH = "runtime/bin/jcommander-1.29.jar:lib/asm-all-4.0.jar:runtime/bin/mcp_deobfuscate-1.2.jar"
MAIN_CLASS = "org.ldg.mcpd.MCPDeobfuscate"
BASIC_COMMAND = ["java", "-classpath", CLASSPATH, MAIN_CLASS]
class Library(object):
    def __init__(self, filename):
        self.name = os.path.basename(filename)

        self.obf = filename # usually os.path.join(OBF_LIBS, self.name)
                            # but not for minecraft{,_server}.jar
        self.obf_inh = os.path.join(OBF_LIBS, self.name + ".inh")

        self.deobf = os.path.join(DEOBF_LIBS, self.name)
        self.deobf_inh = self.deobf + ".inh"

    def build_inh(self, obfuscated):
        if obfuscated:
            lib = self.obf
            inheritance = self.obf_inh
        else:
            lib = self.deobf
            inheritance = self.deobf_inh

        command = BASIC_COMMAND + ["--inheritance", inheritance,
                                        "--indir", "/",
                                        "--infiles", lib]

        call_or_die(command)
        return inheritance

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
        else: #if side == SERVER:
            source = os.path.join(self.dir, "src", "server")
            resources = os.path.join(self.dir, "resources", "server")

        if not self.hide_source:
            ## Collect and package source files.
            # Common first, so they can be overridden.
            common_source = os.path.join(self.dir, "src", "common")
            if os.path.isdir(common_source) and os.listdir(common_source):
                # To package these, we just change to the appropriate directory
                # and let self.zip command find everything in it.
                os.chdir(common_source)
                self.zip(package)
                created = True


            if os.path.isdir(source) and os.listdir(source):
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
            self.zip(package)
            created = True

        if os.path.isdir(resources):
            os.chdir(resources)
            self.zip(package)
            created = True

        os.chdir(BASE)
        return created

libraries = []
for file in os.listdir(OBF_LIBS):
    base, extension = os.path.splitext(file)

    if extension.lower() in [".jar", ".zip"]:
        libraries.append(Library(os.path.join(OBF_LIBS, file)))

minecraft_jars = []
if os.path.exists(OBF_CLIENT):
    library = Library(OBF_CLIENT)
    library.deobf = DEOBF_CLIENT
    minecraft_jars.append(library)
if os.path.exists(OBF_SERVER):
    library = Library(OBF_SERVER)
    library.deobf = DEOBF_SERVER
    minecraft_jars.append(library)

obf_inheritances = []
print "---Creating obfuscated inheritance tables---"
for library in minecraft_jars + libraries:
    print library.name + "..."
    obf_inheritances.append(library.build_inh(obfuscated=True))

print "---Obfuscated inheritance tables complete---"
print
print "---Deobfuscating libraries---"
obf_libraries = map(lambda library: library.name, libraries)

if obf_libraries:
    command = BASIC_COMMAND + ["--stored_inheritance"] + obf_inheritances + [
                               "--config", SRG,
                               "--indir", OBF_LIBS, "--outdir", DEOBF_LIBS,
                               "--infiles"] + obf_libraries

    call_or_die(command)

print "---Libraries deobfuscated---"
print
print "---Creating deobfuscated inheritance tables---"
for library in minecraft_jars + libraries:
    print library.name + "..."
    obf_inheritances.append(library.build_inh(obfuscated=False))

print "---Deobfuscated inheritance tables complete---"
print
print "Library deobfuscation complete."
