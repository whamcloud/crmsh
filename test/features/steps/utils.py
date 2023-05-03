import difflib
import tarfile
import glob
import re
import socket
from crmsh import utils, bootstrap, parallax, userdir


COLOR_MODE = r'\x1b\[[0-9]+m'


def get_file_type(file_path):
    rc, out, _ = utils.get_stdout_stderr("file {}".format(file_path))
    if re.search(r'{}: bzip2'.format(file_path), out):
        return "bzip2"
    if re.search(r'{}: directory'.format(file_path), out):
        return "directory"


def get_all_files(archive_path):
    archive_type = get_file_type(archive_path)
    if archive_type == "bzip2":
        with tarfile.open(archive_path) as tar:
            return tar.getnames()
    if archive_type == "directory":
        all_files = glob.glob("{}/*".format(archive_path)) + glob.glob("{}/*/*".format(archive_path))
        return all_files


def file_in_archive(f, archive_path):
    for item in get_all_files(archive_path):
        if re.search(r'/{}$'.format(f), item):
            return True
    return False


def me():
    return socket.gethostname()


def _wrap_cmd_non_root(cmd):
    """
    When running command under sudoer, or the current user is not root,
    wrap crm cluster join command with '<user>@', and for the -N option, too
    """
    user = ""
    sudoer = userdir.get_sudoer()
    current_user = userdir.getuser()
    if sudoer:
        user = sudoer
    elif current_user != 'root':
        user = current_user
    else:
        return cmd
    if "cluster join" in cmd and "@" not in cmd:
        return re.sub("-c *[\'\"]?([^\s]\S+)[\'\"]?", f"-c {user}@\\1", cmd)
    elif "cluster init" in cmd and "-N" in cmd and "@" not in cmd:
        return re.sub("-N *[\'\"]?([^\s]\S+)[\'\"]?", f"-N {user}@\\1", cmd)
    elif "cluster init" in cmd and "--node" in cmd and "@" not in cmd:
        search_patt = r"--node [\'\"](.*)[\'\"]"
        res = re.search(search_patt, cmd)
        if res:
            node_str = ' '.join([f"{user}@{n}" for n in res.group(1).split()])
            return re.sub(search_patt, f"--node '{node_str}'", cmd)
    return cmd


def run_command(context, cmd, exit_on_fail=True):
    cmd = _wrap_cmd_non_root(cmd)
    rc, out, err = utils.get_stdout_stderr(cmd)
    context.return_code = rc
    if out:
        out = re.sub(COLOR_MODE, '', out)
        context.stdout = out
    if err:
        err = re.sub(COLOR_MODE, '', err)
        context.stderr = err
    if rc != 0 and exit_on_fail:
        if out:
            context.logger.info("\n{}\n".format(out))
        context.logger.error("\n{}\n".format(err))
        context.failed = True
    return rc, out, err


def run_command_local_or_remote(context, cmd, addr, exit_on_fail=True):
    if addr == me():
        return run_command(context, cmd, exit_on_fail)
    else:
        cmd = _wrap_cmd_non_root(cmd)
        try:
            results = parallax.parallax_call(addr.split(','), cmd)
        except ValueError as err:
            err = re.sub(COLOR_MODE, '', str(err))
            context.stderr = err
            if exit_on_fail:
                context.logger.error("\n{}\n".format(err))
                context.failed = True
        else:
            out = utils.to_ascii(results[0][1][1])
            err = utils.to_ascii(results[0][1][2])
            context.stdout = out
            context.stderr = err
            context.return_code = 0
            return 0, out, err


def check_service_state(context, service_name, state, addr):
    if state not in ["started", "stopped", "enabled", "disabled"]:
        context.logger.error("\nService state should be \"started/stopped/enabled/disabled\"\n")
        context.failed = True

    state_dict = {"started": True,
            "stopped": False,
            "enabled": True,
            "disabled": False}
    if state in ["started", "stopped"]:
        check_func = utils.service_is_active
    else:
        check_func = utils.service_is_enabled

    remote_addr = None if addr == me() else addr
    return check_func(service_name, remote_addr) is state_dict[state]


def check_cluster_state(context, state, addr):
    return check_service_state(context, 'pacemaker.service', state, addr)


def is_unclean(node):
    rc, out, err = utils.get_stdout_stderr("crm_mon -1")
    return "{}: UNCLEAN".format(node) in out


def online(context, nodelist):
    rc = True
    _, out = utils.get_stdout("sudo crm_node -l")
    for node in nodelist.split():
        node_info = "{} member".format(node)
        if not node_info in out:
            rc = False
            context.logger.error("\nNode \"{}\" not online\n".format(node))
    return rc

def assert_eq(expected, actual):
    if expected != actual:
        msg = "\033[32m" "Expected" "\033[31m" " != Actual" "\033[0m" "\n" \
              "\033[32m" "Expected:" "\033[0m" " {}\n" \
              "\033[31m" "Actual:" "\033[0m" " {}".format(expected, actual)
        if isinstance(expected, str) and '\n' in expected:
            try:
                diff = '\n'.join(difflib.unified_diff(
                    expected.splitlines(),
                    actual.splitlines(),
                    fromfile="expected",
                    tofile="actual",
                    lineterm="",
                ))
                msg = "{}\n" "\033[31m" "Diff:" "\033[0m" "\n{}".format(msg, diff)
            except Exception:
                pass
        raise AssertionError(msg)
