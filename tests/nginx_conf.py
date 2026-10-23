"""nginx's configuration as the container builds it, for the tests (no nginx here): nginx/servers.sh writes one server
block per https name (audit 2026-10-09b, B1), the image fills the ${...} of askurious.conf.template (envsubst, only
the variables it has), and the includes of the server blocks and of nginx/locations/ are put in place."""
import os
import re

from tests.helpers import bounded_run

from tests.platform.test_audit8_b_deploy import BASH, BASH_SLOW

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NGINX = os.path.join(HERE, "deploy", "docker", "nginx")

# compose.yaml's defaults and a further brand with its participants' name
ENV = {"RESEARCHER_HOST": "askurious.de", "PARTICIPANT_HOST": "ask.askurious.de", "STATUS_HOST": "status.askurious.de",
       "BRAND_RESEARCHER_HOSTS": "polltergeist.de", "BRAND_PARTICIPANT_HOSTS": "ask.polltergeist.de",
       "BRAND_STACK_DOMAIN": "", "OLD_RESEARCHER_HOST": "queryous.de", "OLD_PARTICIPANT_HOST": "ask.queryous.de",
       "OLD_STATUS_HOST": "status.queryous.de", "WWW_REDIRECT": "1"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def servers(tmp_path, **env):
    """Run servers.sh: (servers.conf, the list of names, stderr)."""
    folder = tmp_path / "servers"
    values = dict(ENV, **env)
    run_env = dict(os.environ, SERVERS_DIR=folder.as_posix(), **values)
    out = bounded_run.run([BASH, os.path.join(NGINX, "servers.sh").replace("\\", "/")], env=run_env, text=True,
                          timeout=int(os.environ.get("BASH_TEST_TIMEOUT", str(60 * BASH_SLOW))))
    assert out.returncode == 0, out.stderr
    return (folder / "servers.conf").read_text(), (folder / "names").read_text().split(), out.stderr


def render(tmp_path, **env):
    """The whole configuration nginx reads, includes put in place."""
    values = dict(ENV, **env)
    conf = re.sub(r"\$\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)),
                  _read(os.path.join(NGINX, "askurious.conf.template")))
    blocks = servers(tmp_path, **env)[0]
    conf = conf.replace("include /etc/nginx/servers/*.conf;", blocks)
    # a location file may include another (researcher.conf and participant.conf: wartung.conf)
    for _ in range(3):
        conf = re.sub(r"include /etc/nginx/locations/(\w+)\.conf;",
                      lambda m: _read(os.path.join(NGINX, "locations", m.group(1) + ".conf")), conf)
    return conf


def server_blocks(conf):
    """{server_name: text} of the https server blocks with a name (one name each)."""
    found = {}
    for block in re.findall(r"^server \{\n(.*?)^\}$", conf, re.S | re.M):
        m = re.search(r"^\s*server_name (\S+);", block, re.M)
        if m and m.group(1) != "_":
            found[m.group(1)] = block
    return found
