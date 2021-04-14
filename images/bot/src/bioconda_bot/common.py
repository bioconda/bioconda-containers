import logging
import os
import re
import sys
from asyncio import gather, sleep
from asyncio.subprocess import create_subprocess_exec
from pathlib import Path
from shutil import which
from typing import Any, Dict, List, Optional, Set, Tuple

from aiohttp import ClientSession
from yaml import safe_load

logger = logging.getLogger(__name__)
log = logger.info


async def async_exec(
    command: str, *arguments: str, env: Optional[Dict[str, str]] = None
) -> None:
    process = await create_subprocess_exec(command, *arguments, env=env)
    return_code = await process.wait()
    if return_code != 0:
        raise RuntimeError(
            f"Failed to execute {command} {arguments} (return code: {return_code})"
        )


# Post a comment on a given issue/PR with text in message
async def send_comment(session: ClientSession, issue_number: int, message: str) -> None:
    token = os.environ["BOT_TOKEN"]
    url = (
        f"https://api.github.com/repos/bioconda/bioconda-recipes/issues/{issue_number}/comments"
    )
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    payload = {"body": message}
    log("Sending comment: url=%s", url)
    log("Sending comment: payload=%s", payload)
    async with session.post(url, headers=headers, json=payload) as response:
        status_code = response.status
        log("the response code was %d", status_code)
        if status_code < 200 or status_code > 202:
            sys.exit(1)


# Return true if a user is a member of bioconda
async def is_bioconda_member(session: ClientSession, user: str) -> bool:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/orgs/bioconda/members/{user}"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    rc = 404
    async with session.get(url, headers=headers) as response:
        try:
            response.raise_for_status()
            rc = response.status
        except:
            # Do nothing, this just prevents things from crashing on 404
            pass

    return rc == 204


# Fetch and return the JSON of a PR
# This can be run to trigger a test merge
async def get_pr_info(session: ClientSession, pr: int) -> Any:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{pr}"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    pr_info = safe_load(res)
    return pr_info


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
) -> bool:
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
        return False

    return await approval_review(session, issue_number)


def parse_circle_ci_summary(summary: str) -> List[str]:
    return re.findall(r"gh/bioconda/bioconda-recipes/(\d+)", summary)


# Parse the summary string returned by github to get the CircleCI run ID
# Given a CircleCI run ID, return a list of its tarball artifacts
async def fetch_artifacts(session: ClientSession, circle_ci_id: str) -> Set[str]:
    url = f"https://circleci.com/api/v1.1/project/github/bioconda/bioconda-recipes/{circle_ci_id}/artifacts"
    log("contacting circleci %s", url)
    async with session.get(url) as response:
        # Sometimes we get a 301 error, so there are no longer artifacts available
        if response.status == 301:
            return set()
        res = await response.text()

    if len(res) < 3:
        return set()

    res = res.replace("(", "[").replace(")", "]")
    res = res.replace("} ", "}, ")
    res = res.replace(":node-index", '"node-index":')
    res = res.replace(":path", '"path":')
    res = res.replace(":pretty-path", '"pretty-path":')
    res = res.replace(":url", '"url":')
    res_object = safe_load(res)
    artifacts = {
        artifact["url"]
        for artifact in res_object
        if artifact["url"].endswith(
            (
                ".tar.gz",
                ".tar.bz2",
                "/repodata.json",
            )
        )
    }
    return artifacts


# Given a PR and commit sha, fetch a list of the artifacts
async def fetch_pr_sha_artifacts(session: ClientSession, pr: int, sha: str) -> List[str]:
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/commits/{sha}/check-runs"
    artifacts: List[str] = []

    headers = {
        "User-Agent": "BiocondaCommentResponder",
        "Accept": "application/vnd.github.antiope-preview+json",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    check_runs = safe_load(res)

    for check_run in check_runs["check_runs"]:
        if check_run["output"]["title"] == "Workflow: bioconda-test":
            # The circleci IDs are embedded in a string in output:summary
            circle_ci_ids = parse_circle_ci_summary(check_run["output"]["summary"])
            for item in circle_ci_ids:
                artifact = await fetch_artifacts(session, item)
                artifacts.extend(artifact)
    return artifacts


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
async def merge_pr(session: ClientSession, pr: int) -> None:
    token = os.environ["BOT_TOKEN"]
    await send_comment(
        session,
        pr,
        "I will attempt to upload artifacts and merge this PR. This may take some time, please have patience.",
    )

    try:
        mergeable = await check_is_mergeable(session, pr)
        log("mergeable state of %s is %s", pr, mergeable)
        if not mergeable:
            await send_comment(session, pr, "Sorry, this PR cannot be merged at this time.")
        else:
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


async def get_pr_comment() -> Tuple[Any, Optional[int], Optional[str]]:
    job_context = safe_load(os.environ["JOB_CONTEXT"])
    log("%s", job_context)
    if job_context["event"]["issue"].get("pull_request") is None:
        return job_context, None, None
    issue_number = job_context["event"]["issue"]["number"]

    original_comment = job_context["event"]["comment"]["body"]
    log("the comment is: %s", original_comment)
    return job_context, issue_number, original_comment
