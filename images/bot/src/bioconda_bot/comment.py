import logging
import os
import re
import sys

from aiohttp import ClientSession
from yaml import safe_load

from .common import (
    async_exec,
    fetch_pr_sha_artifacts,
    get_pr_comment,
    get_pr_info,
    is_bioconda_member,
    send_comment,
)

logger = logging.getLogger(__name__)
log = logger.info


# Given a PR and commit sha, post a comment with any artifacts
async def make_artifact_comment(session: ClientSession, pr: int, sha: str) -> None:
    artifacts = await fetch_pr_sha_artifacts(session, pr, sha)
    nPackages = sum(1 for artifact in artifacts if artifact.endswith((".tar.bz2", "tar.gz")))

    if nPackages > 0:
        # If a package artifact is found, the accompanying repodata is the preceeding item in artifacts
        comment = "Artifacts built on CircleCI are ready for inspection:\n\n"

        # Table of packages and repodata.json
        comment += "<details><summary>Package(s)</summary>\n\n"
        comment += "Arch | Package | Repodata\n-----|---------|---------\n"
        install_noarch = ""
        install_linux = ""
        install_osx = ""

        for artifact in artifacts:
            if not (package_match := re.match(r"^((.+)\/(.+)\/(.+\.tar\.bz2))$", artifact)):
                continue
            url, basedir, subdir, packageName = package_match.groups()
            repo_url = "/".join([basedir, subdir, "repodata.json"])
            conda_install_url = basedir

            if subdir == "noarch":
                comment += "noarch |"
                install_noarch = (
                    f"```\nconda install -c {conda_install_url} <package name>\n```\n"
                )
            elif subdir == "linux-64":
                comment += "linux-64 |"
                install_linux = (
                    f"```\nconda install -c {conda_install_url} <package name>\n```\n"
                )
            else:
                comment += "osx-64 |"
                install_osx = f"```\nconda install -c {conda_install_url} <package name>\n```\n"
            comment += f" [{packageName}]({url}) | [repodata.json]({repo_url})\n"

        # Conda install examples
        comment += "***\n\nYou may also use `conda` to install these:\n\n"
        if install_noarch:
            comment += f" * For packages on noarch:\n{install_noarch}"
        if install_linux:
            comment += f" * For packages on linux-64:\n{install_linux}"
        if install_osx:
            comment += f" * For packages on osx-64:\n{install_osx}"

        comment += "***\n"
        comment += "</details>\n"
        # Table of containers
        comment += "<details><summary>Container image(s)</summary>\n\n"
        comment += "Package | Tag | Install with `docker`\n"
        comment += "--------|-----|----------------------\n"

        for artifact in artifacts:
            if artifact.endswith(".tar.gz"):
                image_name = artifact.split("/").pop()[: -len(".tar.gz")]
                if "%3A" in image_name:
                    package_name, tag = image_name.split("%3A", 1)
                    comment += f"[{package_name}]({artifact}) | {tag} | "
                    comment += f'<details><summary>show</summary>`curl -L "{artifact}" \\| gzip -dc \\| docker load`</details>\n'
        comment += "</details>\n"
    else:
        comment = (
            "No artifacts found on the most recent CircleCI build. "
            "Either the build failed or the recipe was blacklisted/skipped."
        )
    await send_comment(session, pr, comment)


# Post a comment on a given PR with its CircleCI artifacts
async def artifact_checker(session: ClientSession, issue_number: int) -> None:
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{issue_number}"
    headers = {
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    pr_info = safe_load(res)

    await make_artifact_comment(session, issue_number, pr_info["head"]["sha"])


# Reposts a quoted message in a given issue/PR if the user isn't a bioconda member
async def comment_reposter(session: ClientSession, user: str, pr: int, message: str) -> None:
    if await is_bioconda_member(session, user):
        log("Not reposting for %s", user)
        return
    log("Reposting for %s", user)
    await send_comment(
        session,
        pr,
        f"Reposting for @{user} to enable pings (courtesy of the BiocondaBot):\n\n> {message}",
    )


# Add the "Please review and merge" label to a PR
async def add_pr_label(session: ClientSession, pr: int) -> None:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/issues/{pr}/labels"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    payload = {"labels": ["please review & merge"]}
    async with session.post(url, headers=headers, json=payload) as response:
        response.raise_for_status()


async def gitter_message(session: ClientSession, msg: str) -> None:
    token = os.environ["GITTER_TOKEN"]
    room_id = "57f3b80cd73408ce4f2bba26"
    url = f"https://api.gitter.im/v1/rooms/{room_id}/chatMessages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "BiocondaCommentResponder",
    }
    payload = {"text": msg}
    log("Sending request to %s", url)
    async with session.post(url, headers=headers, json=payload) as response:
        response.raise_for_status()


async def notify_ready(session: ClientSession, pr: int) -> None:
    try:
        await gitter_message(
            session,
            f"PR ready for review: https://github.com/bioconda/bioconda-recipes/pull/{pr}",
        )
    except Exception:
        logger.exception("Posting to Gitter failed", exc_info=True)
        # Do not die if we can't post to gitter!


# This requires that a JOB_CONTEXT environment variable, which is made with `toJson(github)`
async def main() -> None:
    job_context, issue_number, original_comment = await get_pr_comment()
    if issue_number is None or original_comment is None:
        return

    comment = original_comment.lower()
    async with ClientSession() as session:
        if comment.startswith(("@bioconda-bot", "@biocondabot")):
            if "please update" in comment:
                log("This should have been directly invoked via bioconda-bot-update")
                from .update import update_from_master

                await update_from_master(session, issue_number)
            elif " hello" in comment:
                await send_comment(session, issue_number, "Yes?")
            elif " please fetch artifacts" in comment or " please fetch artefacts" in comment:
                await artifact_checker(session, issue_number)
            elif " please merge" in comment:
                log("This should have been directly invoked via bioconda-bot-merge")
                from .merge import merge_pr

                await merge_pr(session, issue_number)
            elif " please add label" in comment:
                await add_pr_label(session, issue_number)
                await notify_ready(session, issue_number)
            # else:
            #    # Methods in development can go below, flanked by checking who is running them
            #      if job_context["actor"] != "dpryan79":
            #          console.log("skipping")
            #          sys.exit(0)
        elif "@bioconda/" in comment:
            await comment_reposter(
                session, job_context["actor"], issue_number, original_comment
            )
