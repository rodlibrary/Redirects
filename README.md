# Rod Library ScholarWorks redirects

Redirects deprecated UNI ScholarWorks (Digital Commons) links on
`scholarworks.uni.edu` to their new homes in Preservica. This repository holds the
redirect list, the Apache rules, and the checks; the Apache server installs from here.

```
redirects.csv  ◀── DCDS: old ScholarWorks address → Preservica address
        │
        │  every change: pull request → Checks (validate, test in Apache) → review → merge
        ▼
main ──every 15 minutes: git pull + scripts/install_maps.sh──▶ Apache on scholarworks.uni.edu
                                                               old addresses → Preservica (301)
```

Every change reaches `main` through a pull request, so a person reviews new redirects
before the server picks them up.

## Files

| File | What it is |
|---|---|
| [`redirects.csv`](redirects.csv) | **Old ScholarWorks addresses and where they go**, maintained by DCDS. See [redirects.csv](#redirectscsv). |
| [`scripts/build_map.py`](scripts/build_map.py) | Validates `redirects.csv` and builds the lookup files Apache reads. |
| [`scripts/install_maps.sh`](scripts/install_maps.sh) | Installs the redirects on the server (run by cron). |
| [`apache/`](apache/) | The Apache rules (`redirects.conf`), an example virtual host, and the "moved" page. |
| [`tests/`](tests/) | The Apache test setup, test data and expected answers. |
| [`settings.env`](settings.env) | Shared settings: the old ScholarWorks hostname and the hostnames redirects may point to. |
| `.github/workflows/checks.yml` | The checks run on every pull request. |

## redirects.csv

One row per old address, two columns:

| Column | What goes in it | Example |
|---|---|---|
| `dc_path` | the old ScholarWorks address, in full | `https://scholarworks.uni.edu/unigreek_images/1` |
| `p_path` | the new Preservica address, in full, or `410` if the item was withdrawn | `https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C9dbdd64c-5ab8-4344-ae48-99fc70625610` |

```csv
dc_path,p_path
https://scholarworks.uni.edu/unigreek_images/1,https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C9dbdd64c-5ab8-4344-ae48-99fc70625610
https://scholarworks.uni.edu/unigreek_images,https://uni.access.preservica.com/portal/en-US/folder/sdb%3ASO%7C...
https://scholarworks.uni.edu/etd/125,410
```

To change it, open `redirects.csv` on GitHub → pencil icon (or upload a replacement
with the same header) → **Commit changes** → *Create a new branch and start a pull
request*. Merge when the checks pass; the change is live within 15 minutes.

What `dc_path` can be:

* **Any page address:** an item (`/unigreek_images/1`), a collection
  (`/unigreek_images`), or a journal article (`/pias/vol94/iss4/3`). Case, a trailing
  slash, `http`/`https` and any `?...` on the visitor's link don't matter.
* **A file address:** `.../cgi/viewcontent.cgi?article=1001&context=unigreek_images`
  or `.../context/unigreek_images/article/1001/viewcontent/photo.jpg`. Old download
  links only redirect if their file address is listed; the page address alone doesn't
  cover them.
* **Not** the home page (`https://scholarworks.uni.edu/`): it always goes to
  `https://digital.library.uni.edu/` (set in the Apache configuration).

Rules the checks enforce (they name the line and the problem):

* `dc_path` must be a full `https://scholarworks.uni.edu/...` address, listed once.
  The same address twice with the same `p_path` is fine; with different ones it's an
  error naming both lines.
* `p_path` must be a full `https://` address on `uni.access.preservica.com` or
  `digital.library.uni.edu`. Copy it from the portal as it is: `sdb%3AIO%7C` must stay
  encoded (a raw `|` is rejected).
* Each row on its own line, and the file ends with a line break. Editing in Excel:
  save as **CSV UTF-8**.

A collection row also catches pages inside the collection that aren't listed, such as
`/unigreek_images/about.html` or `/unigreek_images/index.2.html`.

## What visitors get

| Request | Response |
|---|---|
| an address listed in `redirects.csv` | 301 to where it points |
| a listed file address (`viewcontent.cgi`, `/context/...`) | 301 to where it points |
| a page inside a listed collection | 301 to the collection |
| a listed address marked `410` | 410 Gone, with the "moved" page |
| `https://scholarworks.uni.edu/` | 301 to `https://digital.library.uni.edu/` |
| anything else | 404, with the "moved" page (search link, `library@uni.edu`) |

Redirects are 301 (permanent) so search engines and Google Scholar update their links.
Requests that end in 404 or 410 are logged to `redirects_miss.log`; review it now and
then to see which old addresses people still use that `redirects.csv` is missing.

## Checks

The **Checks** workflow runs on every pull request and every push to `main`:

| Job | What it does |
|---|---|
| `redirects` | Validates every row of `redirects.csv` and attaches the lookup files the server would install (artifact **redirects-preview**). |
| `apache_test` | Runs a real Apache with the rules in `apache/` and checks every kind of redirect, plus every real redirect in `redirects.csv`. |
| `secret_scan` | Looks for passwords or tokens committed by mistake. |

No repository secrets are needed.

## Setup (one time)

You need the Admin role on this repository.

### 1. Protect `main`

Settings → Rules → Rulesets → New branch ruleset: target the default branch, require a
pull request, require the status checks `redirects`, `apache_test` and `secret_scan`,
and block force pushes. GitHub doesn't let people approve their own pull requests, so
add a second person or a team to `.github/CODEOWNERS` before requiring approvals. The
server installs whatever is on `main`, so this keeps an unchecked change off it.

### 2. Secret scanning

Settings → Advanced Security (or Code security): turn on **Secret Protection** and
**Push protection**. They're free for public repositories.

### 3. Set up the server

The server keeps a copy of this repository and installs the redirects from it. Needs
Apache 2.4 with `mod_rewrite` (and `mod_ssl`), `httxt2dbm` (in `httpd-tools` on RHEL,
`apache2-utils` on Debian/Ubuntu), Python 3, git and curl, and outbound HTTPS to
`github.com`. The repository is public, so no token is needed to download it.

```bash
sudo git clone https://github.com/rodlibrary/redirects.git /opt/redirects
sudo mkdir -p /etc/httpd/redirects
sudo cp /opt/redirects/apache/redirects.conf /etc/httpd/redirects/
sudo cp -r /opt/redirects/apache/www /etc/httpd/redirects/www
sudo env SMOKE=0 /opt/redirects/scripts/install_maps.sh     # first install: Apache isn't using it yet
sudo cp /opt/redirects/apache/vhost-example.conf /etc/httpd/conf.d/scholarworks-redirects.conf   # then edit
sudo apachectl configtest && sudo systemctl reload httpd
```

The Apache user must be able to read everything under `/etc/httpd/redirects`. On
Debian/Ubuntu the configuration directory is `/etc/apache2/sites-available/`.

Keep it up to date with `/etc/cron.d/redirects`:

```
*/15 * * * * root cd /opt/redirects && git pull -q --ff-only && scripts/install_maps.sh >> /var/log/redirects-install.log 2>&1
```

`install_maps.sh` validates `redirects.csv`, does nothing if nothing changed, refuses
to drop more than 20% of the redirects at once (`FORCE=1` to accept a deliberate
clean-up), switches to the new lookup files in one step (no Apache reload needed),
tries five redirects against this server and switches back if any fail. It reads
`settings.env`; its own settings are listed at the top of the script. If the server's
certificate isn't trusted locally, set `SMOKE_INSECURE=1` in the cron line.

To roll back by hand: `ls /etc/httpd/redirects/releases`, then
`sudo ln -sfn /etc/httpd/redirects/releases/<stamp> /etc/httpd/redirects/.current.new && sudo mv -Tf /etc/httpd/redirects/.current.new /etc/httpd/redirects/current`.

Before pointing `scholarworks.uni.edu` at the server, test it directly:

```bash
python3 /opt/redirects/tests/check_redirects.py /etc/httpd/redirects/current/sample.tsv --server https://<server-ip>
```

## Test locally

On a Linux machine with Apache and `httxt2dbm` installed:

```bash
python3 scripts/build_map.py --check redirects.csv
sudo tests/run_apache_test.sh            # same as the apache_test check
```

## Things to know

* **ARKs aren't handled here.** ARKs (NAAN 32498) are minted, matched and resolved in
  the separate `rodlibrary/Resolver` repository. The ARK files that used to be here
  (`arks/`, the Mint ARKs and Match ARKs workflows) remain in this repository's
  history, as of commit `34652bd`.
* **History.** Until November 2026 the redirects lived in the GitLab project
  `rod-library/redirects` on `git.uni.edu`. Its files were moved here on 2026-11-16
  without its history; the GitLab project keeps that.
