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

    :param files: A list with the elements being a tuple of the base_dir
                  of all '.tcattr' files and all filenames that we should
                  apply the ACL attributes.
    """

    for tcattr_basedir in {tcattr[0] for tcattr in files}:
        tcattr_file = os.path.join(tcattr_basedir, '.tcattr')
        setfacl_cmd = ['setfacl', f'--restore={tcattr_file}']
        subprocess.run(setfacl_cmd, cwd=tcattr_basedir, check=True)


def set_file_mode(filename, mode):
    """
    Set file mode and ownership if filename is a regular file or a directory.
    If filename is a symbolic link, set just the ownership since it is not
    possible to set the mode (permissions) for a symbolic link in Linux.

    :param filename: Filename to set the mode to.
    :param mode: Mode to be set on the file.
    """

    root_uid = 0
    root_gid = 0

    os.chown(filename, root_uid, root_gid, follow_symlinks=False)

    if not os.path.islink(filename):
        os.chmod(filename, mode)


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

    default_file_mode = 0o660
    default_dir_mode = 0o755
    default_exec_mode = 0o770

    for filename in files:
        mode = default_file_mode
        if os.path.isdir(filename):
            mode = default_dir_mode
        else:
            # Check if file is an executable file
            status = os.stat(filename, follow_symlinks=False)
            if status.st_mode & 0o111:
                mode = default_exec_mode
        set_file_mode(filename, mode)


def remove_links_from_tcattr(base_dir):
    """
    Remove any symbolic link from the '.tcattr' file.
    It's need because we cannot set mode (permissions) for symbolic
    links in Linux.

    :param base_dir: Base directory where there is a '.tcattr' file.
    """

    tcattr = []
    tcattr_file = os.path.join(base_dir, '.tcattr')
    tcattr_file_tmp = os.path.join(base_dir, '.tcattr.tmp')
    field_separator = '%TCB%'

    with open(tcattr_file, 'r') as fd_tcattr:
        for line in fd_tcattr:
            if line.startswith('\n'):
                tcattr.append(field_separator)
            else:
                tcattr.append(line)
    tcattr = ''.join(tcattr)

    with open(tcattr_file_tmp, 'w') as fd_tcattr_tmp:
        for file_attr in tcattr.split(field_separator):
            filename = file_attr.split('\n')[0].replace('# file: ', '')
            if not os.path.islink(os.path.join(base_dir, filename)):
                fd_tcattr_tmp.write(file_attr+'\n')

    os.rename(tcattr_file_tmp, tcattr_file)


def set_acl_attributes(change_dir):
    """
    From "change_dir" onward, find all ".tcattr" files and create two lists
    which the content should be:
      - Files and/or directories that must have ".tcattr" ACLs
      - The other files and/or directories that must have "default" ACLs
    Each ".tcattr" file should be created by the "isolate" command or
    manually by the user.
    Having both lists in hand, set the attributes.

    :param change_dir: Directory with changes to be incoporated into an
                       OSTree commit.
    """

    files_to_apply_tcattr_acl = []
    files_to_apply_default_acl = []

    for base_dir, _, filenames in os.walk(change_dir):
        if '.tcattr' not in filenames:
            continue
        remove_links_from_tcattr(base_dir)
        with open(os.path.join(base_dir, '.tcattr')) as fd_tcattr:
            files_to_apply_tcattr_acl = [
                (base_dir, line.strip().replace('# file: ', ''))
                for line in fd_tcattr
                if '# file: ' in line]

    for base_dir, dirnames, filenames in os.walk(change_dir):
        for filename in dirnames + filenames:
            if filename != '.tcattr' and \
               os.path.join(base_dir, filename) not in [
                       os.path.join(*f)
                       for f in files_to_apply_tcattr_acl]:
                files_to_apply_default_acl.append(os.path.join(base_dir, filename))

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
