"""Union sub-command CLI handling

The union sub-command merges a given OSTree reference (e.g. branch or commit
hash) with local changes (e.g. copied from an adjusted module using the isolate
sub-command).
"""

import os
import logging
import subprocess
from tcbuilder.backend import union as ub
from tcbuilder.errors import PathNotExistError

log = logging.getLogger("torizon." + __name__)


def check_and_append_dirs(changes_dirs, new_changes_dirs, temp_dir):
    """Check and append additional directories with changes"""

    for changes_dir in new_changes_dirs:
        if not os.path.exists(changes_dir):
            raise PathNotExistError(f'Changes directory "{changes_dir}" does not exist')

        os.makedirs(f"{temp_dir}/{changes_dir}")
        # Review: not appropriately handling especial directory names (FIXME).
        cp_command = f"cp -r {changes_dir}/. {temp_dir}/{changes_dir}"
        subprocess.check_output(cp_command, shell=True,
                                stderr=subprocess.STDOUT)
        temp_change_dir = os.path.join(temp_dir, changes_dir)
        set_acl_attributes(temp_change_dir)
        changes_dirs.append(os.path.abspath(temp_change_dir))


def apply_tcattr_acl(files):
    """
    Apply ACLs based on .tcattr files. It just needs to be done once for
    each ".tcattr" file found in each sub directory of the tree.
    """

    # Review: not appropriately handling especial directory names (FIXME).
    for tcattr_basedir in {tcattr[0] for tcattr in files}:
        setfacl_cmd = f"cd {tcattr_basedir} && \
                        setfacl --restore={tcattr_basedir}/.tcattr"
        subprocess.run(setfacl_cmd, shell=True, check=True)


def set_file_mode(filename, mode, is_link=False):
    """
    Set file mode and ownership if filename is a regular file or a directory.
    If filename is a symbolic link, set just the ownership since it is not
    possible to set the mode (permissions) for a symbolic link in Linux.

    :param filename: Filename to set the mode to.
    :param mode: Mode to be set on the file.
    :param is_link: Indicates if file is a symbolic link.
    """

    chown = ['chown', 'root.root', filename]
    if is_link:
        chown.insert(1, '-h') # use '-h' to set the link ownership
    subprocess.run(chown, check=True)

    if not is_link:
        chmod = ['chmod', mode, filename]
        subprocess.run(chmod, check=True)


def apply_default_acl(files):
    """
    Apply default ACL to files and directories.
      - For executables files: 0770.
      - For non-executables files: 0660.
      - For directories: 0755.
      - For symbolic links just the user and group will be set.
      - For all files and directories the user and group will be "root".

    :param files: A list of files to apply default ACL.
    """

    default_file_mode = "0660"
    default_dir_mode = "0755"
    default_exec_mode = "0770"

    for filename in files:
        is_link = False
        mode = default_file_mode
        if os.path.islink(filename):
            is_link = True
        elif os.path.isdir(filename):
            mode = default_dir_mode
        else:
            # Check if file is an executable file
            status = os.stat(filename)
            if status.st_mode & 0o111:
                mode = default_exec_mode
        set_file_mode(filename, mode, is_link)


def set_acl_attributes(change_dir):
    """
    From "change_dir" onward, find all ".tcattr" files and create two lists
    which the contents should be:
      - Files and/or directories that must have ".tcattr" ACLs
      - The other files and/or directories that must have "default" ACLs
    Each ".tcattr" file should be created by the "isolate" command or
    manually by the user.
    """

    files_to_apply_tcattr_acl = []
    files_to_apply_default_acl = []

    for base_dir, _, filenames in os.walk(change_dir):
        if '.tcattr' not in filenames:
            continue
        with open(f'{base_dir}/.tcattr') as fd_tcattr:
            for line in fd_tcattr:
                if '# file: ' in line:
                    line = line.strip().replace('# file: ', '')
                    files_to_apply_tcattr_acl.append((f'{base_dir}', line))

    for base_dir, dirnames, filenames in os.walk(change_dir):
        for filename in dirnames + filenames:
            # Review: Reduce nesting (FIXME).
            if filename != '.tcattr':
                full_filename = f'{base_dir}/{filename}'
                if full_filename not in ['/'.join(f)
                                         for f in files_to_apply_tcattr_acl]:
                    files_to_apply_default_acl.append(full_filename)

    apply_tcattr_acl(files_to_apply_tcattr_acl)
    apply_default_acl(files_to_apply_default_acl)



def union(changes_dirs, extra_changes_dirs, storage_dir,
          union_branch, commit_subject=None, commit_body=None):
    """Perform the actual work of the union subcommand"""

    storage_dir_ = os.path.abspath(storage_dir)
    if not os.path.exists(storage_dir_):
        raise PathNotExistError(f"Storage directory \"{storage_dir_}\""
                                " does not exist.")

    changes_dirs_ = []
    if changes_dirs is None:
        # Automatically add the ones present...
        for subdir in ["changes", "splash", "dt", "kernel"]:
            changed_dir = os.path.join(storage_dir_, subdir)
            if os.path.isdir(changed_dir):
                if subdir == "changes":
                    set_acl_attributes(changed_dir)
                changes_dirs_.append(changed_dir)
    else:
        temp_dir = os.path.join("/tmp", "changes_dirs")
        os.mkdir(temp_dir)
        check_and_append_dirs(changes_dirs_, changes_dirs, temp_dir)

    if extra_changes_dirs:
        temp_dir_extra = os.path.join("/tmp", "extra_changes_dirs")
        os.mkdir(temp_dir_extra)
        check_and_append_dirs(changes_dirs_, extra_changes_dirs, temp_dir_extra)

    src_ostree_archive_dir = os.path.join(storage_dir_, "ostree-archive")

    log.debug(f"union: subject='{commit_subject}' body='{commit_body}'")
    commit = ub.union_changes(changes_dirs_, src_ostree_archive_dir,
                              union_branch, commit_subject, commit_body)
    log.info(f"Commit {commit} has been generated for changes and is ready"
             " to be deployed.")


def do_union(args):
    """Run \"union\" subcommand"""

    union(args.changes_dirs, args.extra_changes_dirs, args.storage_directory,
          args.union_branch, args.subject, args.body)


def init_parser(subparsers):
    """Initialize argument parser"""
    subparser = subparsers.add_parser("union", help="""\
    Create a commit out of isolated changes for unpacked Toradex Easy Installer Image""")
    subparser.add_argument("--changes-directory", dest="changes_dirs", action='append',
                           help="""Path to the directory containing user changes.
                           Can be specified multiple times!""")
    subparser.add_argument("--extra-changes-directory", dest="extra_changes_dirs", action='append',
                           help="""Additional path with user changes to be committed.
                           Can be specified multiple times!""")
    subparser.add_argument("--union-branch", dest="union_branch",
                           help="""Name of branch containing the changes committed to
                           the unpacked repo.
                           """,
                           required=True)
    subparser.add_argument("--subject", dest="subject",
                           help="""OSTree commit subject. Defaults to
                           "TorizonCore Builder [timestamp]"
                           """)
    subparser.add_argument("--body", dest="body",
                           help="""OSTree commit body message""")

    subparser.set_defaults(func=do_union)
