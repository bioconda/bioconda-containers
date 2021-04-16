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


async def get_sha_for_status(job_context: Dict[str, Any]) -> Optional[str]:
    if job_context["event_name"] != "status":
        return None
    log("Got %s event", "status")
    event = job_context["event"]
    if event["state"] != "success":
        return None
    branches = event.get("branches")
    if not branches:
        return None
    sha: Optional[str] = branches[0]["commit"]["sha"]
    log("Use %s event SHA %s", "status", sha)
    return sha


async def get_sha_for_check_suite(job_context: Dict[str, Any]) -> Optional[str]:
    if job_context["event_name"] != "check_suite":
        return None
    log("Got %s event", "check_suite")
    check_suite = job_context["event"]["check_suite"]
    if check_suite["conclusion"] != "success":
        return None
    sha: Optional[str] = check_suite.get("head_sha")
    if not sha:
        pull_requests = check_suite.get("pull_requests")
        if pull_requests:
            sha = pull_requests[0]["head"]["sha"]
    if not sha:
        return None
    log("Use %s event SHA %s", "check_suite", sha)
    return sha


async def get_prs_for_sha(session: ClientSession, sha: str) -> List[int]:
    headers = {
        "User-Agent": "BiocondaCommentResponder",
        "Accept": "application/vnd.github.v3+json",
    }
    pr_numbers: List[int] = []
    per_page = 100
    for page in range(1, 20):
        url = (
            "https://api.github.com/repos/bioconda/bioconda-recipes/pulls"
            f"?per_page={per_page}"
            f"&page={page}"
        )
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            res = await response.text()
        prs = safe_load(res)
        pr_numbers.extend(pr["number"] for pr in prs if pr["head"]["sha"] == sha)
        if len(prs) < per_page:
            break
    return pr_numbers


async def get_sha_for_status_check(job_context: Dict[str, Any]) -> Optional[str]:
    return await get_sha_for_status(job_context) or await get_sha_for_check_suite(job_context)


async def get_job_context() -> Any:
    job_context = safe_load(os.environ["JOB_CONTEXT"])
    log("%s", job_context)
    return job_context


async def get_pr_comment(job_context: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    event = job_context["event"]
    if event["issue"].get("pull_request") is None:
        return None, None
    issue_number = event["issue"]["number"]

    original_comment = event["comment"]["body"]
    log("the comment is: %s", original_comment)
    return issue_number, original_comment
