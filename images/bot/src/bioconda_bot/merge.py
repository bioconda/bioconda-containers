import logging
import os
import re
import sys
from asyncio import gather, sleep
from asyncio.subprocess import create_subprocess_exec
from enum import Enum, auto
from pathlib import Path
from shutil import which
from typing import Any, Dict, List, Optional, Set, Tuple

from aiohttp import ClientSession
from yaml import safe_load

from .common import (
    async_exec,
    fetch_pr_sha_artifacts,
    get_job_context,
    get_pr_comment,
    get_pr_info,
    is_bioconda_member,
    send_comment,
)

logger = logging.getLogger(__name__)
log = logger.info


class MergeState(Enum):
    UNKNOWN = auto()
    MERGEABLE = auto()
    NOT_MERGEABLE = auto()
    NEEDS_REVIEW = auto()
    MERGED = auto()


# Ensure there's at least one approval by a member
async def approval_review(session: ClientSession, issue_number: int) -> bool:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{issue_number}/reviews"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    reviews = safe_load(res)

    approved_reviews = [review for review in reviews if review["state"] == "APPROVED"]
    if not approved_reviews:
        return False

    # Ensure the review author is a member
    return any(
        gather(
            *(
                is_bioconda_member(session, review["user"]["login"])
                for review in approved_reviews
            )
        )
    )


# Check the mergeable state of a PR
async def check_is_mergeable(
    session: ClientSession, issue_number: int, second_try: bool = False
) -> MergeState:
    token = os.environ["BOT_TOKEN"]
    # Sleep a couple of seconds to allow the background process to finish
    if second_try:
        await sleep(3)

    # PR info
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{issue_number}"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    pr_info = safe_load(res)

    # We need mergeable == true and mergeable_state == clean, an approval by a member and
    if pr_info.get("mergeable") is None and not second_try:
        return await check_is_mergeable(session, issue_number, True)
    elif (
        pr_info.get("mergeable") is None
        or not pr_info["mergeable"]
        or pr_info["mergeable_state"] != "clean"
    ):
        return MergeState.NOT_MERGEABLE

    if not await approval_review(session, issue_number):
        return MergeState.NEEDS_REVIEW
    return MergeState.MERGEABLE


def parse_circle_ci_summary(summary: str) -> List[str]:
    return re.findall(r"gh/bioconda/bioconda-recipes/(\d+)", summary)


# Ensure uploaded containers are in repos that have public visibility
async def toggle_visibility(session: ClientSession, container_repo: str) -> None:
    url = f"https://quay.io/api/v1/repository/biocontainers/{container_repo}/changevisibility"
    QUAY_OAUTH_TOKEN = os.environ["QUAY_OAUTH_TOKEN"]
    headers = {
        "Authorization": f"Bearer {QUAY_OAUTH_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {"visibility": "public"}
    rc = 0
    try:
        async with session.post(url, headers=headers, json=body) as response:
            rc = response.status
    except:
        # Do nothing
        pass
    log("Trying to toggle visibility (%s) returned %d", url, rc)


# Download an artifact from CircleCI, rename and upload it
async def download_and_upload(session: ClientSession, x: str) -> None:
    basename = x.split("/").pop()
    # the tarball needs a regular name without :, the container needs pkg:tag
    image_name = basename.replace("%3A", ":").replace("\n", "").replace(".tar.gz", "")
    file_name = basename.replace("%3A", "_").replace("\n", "")

    async with session.get(x) as response:
        with open(file_name, "wb") as file:
            logged = 0
            loaded = 0
            while chunk := await response.content.read(256 * 1024):
                file.write(chunk)
                loaded += len(chunk)
                if loaded - logged >= 50 * 1024 ** 2:
                    log("Downloaded %.0f MiB: %s", max(1, loaded / 1024 ** 2), x)
                    logged = loaded
            log("Downloaded %.0f MiB: %s", max(1, loaded / 1024 ** 2), x)

    if x.endswith(".gz"):
        # Container
        log("uploading with skopeo: %s", file_name)
        # This can fail, retry with 5 second delays
        count = 0
        maxTries = 5
        success = False
        QUAY_LOGIN = os.environ["QUAY_LOGIN"]
        env = os.environ.copy()
        # TODO: Fix skopeo package to find certificates on its own.
        skopeo_path = which("skopeo")
        if not skopeo_path:
            raise RuntimeError("skopeo not found")
        env["SSL_CERT_DIR"] = str(Path(skopeo_path).parents[1].joinpath("ssl"))
        while count < maxTries:
            try:
                await async_exec(
                    "skopeo",
                    "--command-timeout",
                    "600s",
                    "copy",
                    f"docker-archive:{file_name}",
                    f"docker://quay.io/biocontainers/{image_name}",
                    "--dest-creds",
                    QUAY_LOGIN,
                    env=env,
                )
                success = True
                break
            except:
                count += 1
                if count == maxTries:
                    raise
            await sleep(5)
        if success:
            await toggle_visibility(session, basename.split("%3A")[0])
    elif x.endswith(".bz2"):
        # Package
        log("uploading package")
        ANACONDA_TOKEN = os.environ["ANACONDA_TOKEN"]
        await async_exec("anaconda", "-t", ANACONDA_TOKEN, "upload", file_name, "--force")

    log("cleaning up")
    os.remove(file_name)


# Upload artifacts to quay.io and anaconda, return the commit sha
# Only call this for mergeable PRs!
async def upload_artifacts(session: ClientSession, pr: int) -> str:
    # Get last sha
    pr_info = await get_pr_info(session, pr)
    sha: str = pr_info["head"]["sha"]

    # Fetch the artifacts
    artifacts = await fetch_pr_sha_artifacts(session, pr, sha)
    artifacts = [artifact for artifact in artifacts if artifact.endswith((".gz", ".bz2"))]
    assert artifacts

    # Download/upload Artifacts
    for artifact in artifacts:
        await download_and_upload(session, artifact)

    return sha


# Assume we have no more than 250 commits in a PR, which is probably reasonable in most cases
async def get_pr_commit_message(session: ClientSession, issue_number: int) -> str:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{issue_number}/commits"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    commits = safe_load(res)
    message = "".join(f" * {commit['commit']['message']}\n" for commit in reversed(commits))
    return message


# Merge a PR
async def merge_pr(session: ClientSession, pr: int, init_message: str) -> MergeState:
    token = os.environ["BOT_TOKEN"]
    mergeable = await check_is_mergeable(session, pr)
    log("mergeable state of %s is %s", pr, mergeable)
    if mergeable is not MergeState.MERGEABLE:
        return mergeable

    if init_message:
        await send_comment(session, pr, init_message)
    try:
        log("uploading artifacts")
        sha = await upload_artifacts(session, pr)
        log("artifacts uploaded")

        # Carry over last 250 commit messages
        msg = await get_pr_commit_message(session, pr)

        # Hit merge
        url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{pr}/merge"
        headers = {
            "Authorization": f"token {token}",
            "User-Agent": "BiocondaCommentResponder",
        }
        payload = {
            "sha": sha,
            "commit_title": f"[ci skip] Merge PR {pr}",
            "commit_message": f"Merge PR #{pr}, commits were: \n{msg}",
            "merge_method": "squash",
        }
        log("Putting merge commit")
        async with session.put(url, headers=headers, json=payload) as response:
            rc = response.status
        log("body %s", payload)
        log("merge_pr the response code was %s", rc)
    except:
        await send_comment(
            session,
            pr,
            "I received an error uploading the build artifacts or merging the PR!",
        )
        logger.exception("Upload failed", exc_info=True)
    return MergeState.MERGED


async def request_merge(session: ClientSession, pr: int) -> MergeState:
    init_message = "I will attempt to upload artifacts and merge this PR. This may take some time, please have patience."
    merged = await merge_pr(session, pr, init_message)
    if merged is MergeState.NOT_MERGEABLE:
        await send_comment(session, pr, "Sorry, this PR cannot be merged at this time.")
    return merged


# This requires that a JOB_CONTEXT environment variable, which is made with `toJson(github)`
async def main() -> None:
    job_context = await get_job_context()
    issue_number, original_comment = await get_pr_comment(job_context)
    if issue_number is None or original_comment is None:
        return

    comment = original_comment.lower()
    if comment.startswith(("@bioconda-bot", "@biocondabot")):
        if " please merge" in comment:
            async with ClientSession() as session:
                await request_merge(session, issue_number)
