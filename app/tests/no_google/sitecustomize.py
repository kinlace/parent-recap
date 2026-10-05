"""Google's packages as if they weren't installed: the `google` extra, which install.sh adds only
for google mode. Tests put this folder on a process's PYTHONPATH, and test_without_google_packages.py
loads the same blocker in-process."""
import importlib.abc
import sys

# The top-level modules of the three packages in the `google` extra and of what they bring in.
BLOCKED = ("google", "googleapiclient", "google_auth_oauthlib", "google_auth_httplib2", "httplib2",
           "oauthlib", "requests_oauthlib", "cryptography", "uritemplate")


class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


if __name__ == "sitecustomize":
    sys.meta_path.insert(0, Blocker())
