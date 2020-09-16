import logging
import os
import shutil
import gi
gi.require_version("OSTree", "1.0")
import traceback
from tcbuilder.errors import TorizonCoreBuilderError
from tcbuilder.backend import dt
from tcbuilder.backend import ostree
from tcbuilder.backend.common import checkout_git_repo

# If OSTree finds a file named `devicetree` it will consider it as the only relevant
# device tree to deploy.
DT_OUTPUT_NAME = "devicetree"

def get_dt_changes_dir(devicetree_out, arg_storage_dir):
    dt_out = ""
    storage_dir = os.path.abspath(arg_storage_dir)
    src_ostree_archive_dir = os.path.join(storage_dir, "ostree-archive")

    repo = ostree.open_ostree(src_ostree_archive_dir)
    kernel_version = ostree.get_kernel_version(repo, ostree.OSTREE_BASE_REF)

    if devicetree_out is None:
        dt_out = os.path.join(storage_dir, "dt")
        dt_out = os.path.join(dt_out, "usr/lib/modules", kernel_version, DT_OUTPUT_NAME)
    else:
        dt_out = os.path.join(devicetree_out, "usr/lib/modules", kernel_version, DT_OUTPUT_NAME)

    return dt_out

def create_dt_changes_dir(devicetree_out, arg_storage_dir):
    dt_out = ""
    dt_out = get_dt_changes_dir(devicetree_out, arg_storage_dir)

    if devicetree_out is None:
        storage_dir = os.path.abspath(arg_storage_dir)
        if os.path.exists(os.path.join(storage_dir, "dt")):
            shutil.rmtree(os.path.join(storage_dir, "dt"))

    os.makedirs(dt_out.rsplit('/', 1)[0])
    return dt_out

def dt_overlay_subcommand(args):
    log = logging.getLogger("torizon." + __name__)  # use name hierarchy for "main" to be the parent

    devicetree_bin = ""
    if args.devicetree_bin is None:
        # if device tree is not provided, it should be the already created one
        devicetree_bin = get_dt_changes_dir(None, args.storage_directory)
        if not os.path.exists(devicetree_bin):
            log.error(f"{devicetree_bin} does not exist")
            return

        # if devicetree binary and devicetree_out is not provided, these files are to be used
        # from /{storage_dir}/dt/usr/lib/modules/<kver>. So copy already created devicetree from
        # volume to workdir, becuase internal volume dt/ directory is deleted before proceeding
        # further to be able to handle all cases for user provided files
        if args.devicetree_out is None:
            shutil.copyfile(devicetree_bin, "devicetree_tmp")
            devicetree_bin = "devicetree_tmp"

        log.info("Device tree from internal volume is to be used")
    else:
        devicetree_bin = os.path.abspath(args.devicetree_bin)

    devicetree_out = ""
    if args.devicetree_out is not None:
        devicetree_out = os.path.abspath(args.devicetree_out)
        if not os.path.exists(devicetree_out):
            log.error(f"{args.devicetree_out} does not exist")
            return
        if os.path.exists(os.path.join(devicetree_out, "usr")):
            log.error(f"{args.devicetree_out} is not empty")
            return

    devicetree_out = create_dt_changes_dir(args.devicetree_out, args.storage_directory)

    dt.build_and_apply(devicetree_bin, args.overlays, devicetree_out,
                       args.include_dir)

    if os.path.exists("devicetree_tmp"):
        os.remove("devicetree_tmp")

    log.info(f"Overlays {args.overlays} successfully applied")

def dt_custom_subcommand(args):
    log = logging.getLogger("torizon." + __name__)  # use name hierarchy for "main" to be the parent

    devicetree_out = ""
    if args.devicetree_out is not None:
        devicetree_out = os.path.abspath(args.devicetree_out)
        if not os.path.exists(devicetree_out):
            log.error(f"{args.devicetree_out} does not exist")
            return
        if os.path.exists(os.path.join(devicetree_out, "usr")):
            log.error(f"{args.devicetree_out} is not empty")
            return

    devicetree_out = create_dt_changes_dir(args.devicetree_out, args.storage_directory)

    dt.build_and_apply(args.devicetree, None, devicetree_out,
                       args.include_dir)

    log.info(f"Device tree {args.devicetree} built successfully")

def dt_checkout_subcommand(args):
    log = logging.getLogger("torizon." + __name__)  # use name hierarchy for "main" to be the parent

    storage_dir = os.path.abspath(args.storage_directory)

    if args.git_repo is None:
        if args.git_branch is not None:
            log.error("git repo is not provided")
            return
        elif os.path.exists(os.path.abspath("device-tree-overlays")):
            log.error("'device-tree-overlays' directory already exists")
            return
    elif args.git_repo is not None:
        if args.git_branch is None:
            log.error("git branch is not provided")
            return
        elif (args.git_repo.startswith("https://") or
            args.git_repo.startswith("git://")):
            repo_name = args.git_repo.rsplit('/', 1)[1].rsplit('.', 1)[0]
            if os.path.exists(os.path.abspath(repo_name)):
                log.error(f"directory '{repo_name}' named as repo name should not exist")
                return
        elif not os.path.exists(os.path.abspath(args.git_repo)):
            log.error(f"{args.git_repo} directory does not exist")
            return

    try:
        checkout_git_repo(storage_dir, args.git_repo, args.git_branch)
        log.info("dt checkout completed successfully")
    except TorizonCoreBuilderError as ex:
        log.error(ex.msg)  # msg from all kinds of Exceptions
        if ex.det is not None:
            log.info(ex.det)  # more elaborative message
        log.debug(traceback.format_exc())  # full traceback to be shown for debugging only

def add_overlay_parser(parser):
    subparsers = parser.add_subparsers(title='Commands:', required=True, dest='cmd')
    subparser = subparsers.add_parser("overlay", help="Apply an overlay")
    subparser.add_argument("--devicetree", dest="devicetree_bin",
                           help="Path to the devicetree binary")
    subparser.add_argument("--devicetree-out", dest="devicetree_out",
                           help="""Path to the devicetree output directory. 
                           Device tree file is stored with name 'devicetree'.""")
    subparser.add_argument("--include-dir", dest="include_dir", action='append',
                           help="""Directory with device tree include (.dtsi) or
                           header files. Can be passed multiple times.""",
                           required=True)
    subparser.add_argument(metavar="overlays", dest="overlays", nargs="+",
                           help="The overlay(s) to apply")

    subparser.set_defaults(func=dt_overlay_subcommand)

    subparser = subparsers.add_parser("custom", help="Compile device tree")
    subparser.add_argument("--devicetree", dest="devicetree",
                           help="Path to the devicetree file",
                           required=True)
    subparser.add_argument("--devicetree-out", dest="devicetree_out",
                           help="""Path to the devicetree output directory.
                           Device tree file is stored with name 'devicetree'.""")
    subparser.add_argument("--include-dir", dest="include_dir", action='append',
                           help="""Directory with device tree include (.dtsi) or
                           header files. Can be passed multiple times.""",
                           required=True)

    subparser.set_defaults(func=dt_custom_subcommand)

    subparser = subparsers.add_parser("checkout", help="checkout a git branch from remote repository")
    subparser.add_argument("--repository", dest="git_repo",
                           help="""Remote repository URL. Default repo is
                           https://github.com/toradex/device-tree-overlays""")
    subparser.add_argument("--branch", dest="git_branch",
                           help="""Branch to be checked out. Default branch with default repo is
                           toradex_<kmajor>.<kminor>.<x>""")

    subparser.set_defaults(func=dt_checkout_subcommand)

def init_parser(subparsers):
    subparser = subparsers.add_parser("dt", help="""\
    Compile and apply device trees and device tree overlays.
    """)

    add_overlay_parser(subparser)
