#!/usr/bin/env python3

import argparse
import os
import re
import sys
import tempfile
import logging

from github import Github, GithubException
from git import Repo, GitCommandError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def is_pull_request():
    return '--pull-request' in sys.argv[1:]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=str, required=True, help='Github repository name')
    parser.add_argument('--base-branch', type=str, default='refs/heads/next', help='Base branch')
    parser.add_argument('--commits', default=None, type=str, help='Range of promoted commits.')
    parser.add_argument('--pull-request', type=int, help='Pull request number to be backported')
    parser.add_argument('--head-commit', type=str, required=is_pull_request(), help='The HEAD of target branch after the pull request specified by --pull-request is merged')
    return parser.parse_args()


def create_pull_request(repo, new_branch_name, base_branch_name, pr, backport_pr_title, commits, is_draft=False):
    pr_body = f'{pr.body}\n\n'
    for commit in commits:
        pr_body += f'- (cherry picked from commit {commit})\n\n'
    pr_body += f'Parent PR: #{pr.number}'
    if is_draft:
        new_branch_name = f'{pr.user.login}:{new_branch_name}'
    try:
        backport_pr = repo.create_pull(
            title=backport_pr_title,
            body=pr_body,
            head=new_branch_name,
            base=base_branch_name,
            draft=is_draft
        )
        logging.info(f"Pull request created: {backport_pr.html_url}")
        backport_pr.add_to_assignees(pr.user)
        logging.info(f"Assigned PR to original author: {pr.user}")
        return backport_pr
    except GithubException as e:
        if 'A pull request already exists' in str(e):
            logging.warning(f'A pull request already exists for {pr.user}:{new_branch_name}')
        else:
            logging.error(f'Failed to create PR: {e}')


def get_pr_commits(repo, pr, stable_branch, start_commit=None):
    commits = []
    if pr.merged:
        merge_commit = repo.get_commit(pr.merge_commit_sha)
        if len(merge_commit.parents) > 1:  # Check if this merge commit include multiple commits
            commits.append(pr.merge_commit_sha)
        else:
            if start_commit:
                promoted_commits = repo.compare(start_commit, stable_branch).commits
            else:
                promoted_commits = repo.get_commits(sha=stable_branch)
            for commit in pr.get_commits():
                for promoted_commit in promoted_commits:
                    commit_title = commit.commit.message.splitlines()[0]
                    # In Scylla-pkg and scylla-dtest for example, we don't create a merge commit for a PR with multiple commits,
                    # according to the GitHub API, the last commit will be the merge commit which is not what we need when backporting (we need all the commits).
                    # So here, we are validating the correct SHA for each commit so we can cherry-pick
                    if promoted_commit.commit.message.startswith(commit_title):
                        commits.append(promoted_commit.sha)
                else:
                    commits.append(start_commit)

    elif pr.state == 'closed':
        events = pr.get_issue_events()
        for event in events:
            if event.event == 'closed':
                commits.append(event.commit_id)
    return commits


def backport(repo, pr, version, commits, backport_base_branch):
    with (tempfile.TemporaryDirectory() as local_repo_path):
        try:
            new_branch_name = f'backport/{pr.number}/to-{version}'
            backport_pr_title = f'[Backport {version}] {pr.title}'
            repo_local = Repo.clone_from(f'https://github.com/{repo.full_name}.git', local_repo_path, branch=backport_base_branch)
            repo_local.git.checkout(b=new_branch_name)
            fork_repo = pr.user.get_repo(repo.full_name.split('/')[1])
            repo_local.create_remote('fork', fork_repo.clone_url)
            remote = 'origin'
            is_draft = False
            for commit in commits:
                try:
                    repo_local.git.cherry_pick(commit, '-m1', '-x')
                except GitCommandError as e:
                    logging.warning(f'Cherry-pick conflict on commit {commit}: {e}')
                    remote = 'fork'
                    is_draft = True
                    repo_local.git.add(A=True)
                    repo_local.git.cherry_pick('--continue')
            repo_local.git.push(remote, new_branch_name, force=True)
            create_pull_request(repo, new_branch_name, backport_base_branch, pr, backport_pr_title, commits,
                                is_draft=is_draft)
        except GitCommandError as e:
            logging.warning(f"GitCommandError: {e}")


def get_prs_from_commits(repo, commits):
    for sha1 in commits:
        commit = repo.get_commit(sha1.sha)
        for parent in commit.parents:
            prs = repo.get_pulls(state="closed", head=parent.sha)
            if prs:
                yield prs[0]
                break


def main():
    args = parse_args()
    base_branch = args.base_branch.split('/')[2]
    promoted_label = 'promoted-to-master'
    repo_name = args.repo
    if args.repo in ('scylladb/scylla', 'scylladb/scylla-enterprise'):
        stable_branch = base_branch
        backport_branch = 'branch-'
        if args.repo == 'scylladb/scylla-enterprise':
            promoted_label = 'promoted-to-enterprise'
    else:
        backport_branch = f'{base_branch}-'
        if base_branch == 'next':
            stable_branch = 'master'
        else:
            stable_branch = base_branch.replace('next', 'branch')

    github_token = os.getenv("BACKPORT_GITHUB_TOKEN")
    backport_label_pattern = re.compile(r'backport/\d+\.\d+$')

    g = Github(github_token)
    repo = g.get_repo(repo_name)
    closed_prs = []
    start_commit = None

    if args.commits:
        start_commit, end_commit = args.commits.split('..')
        commits = repo.compare(start_commit, end_commit).commits
        prs = get_prs_from_commits(repo, commits)
        closed_prs = list(prs)
    if args.pull_request:
        start_commit = args.head_commit
        pr = repo.get_pull(args.pull_request)
        closed_prs = [pr]

    for pr in closed_prs:
        labels = [label.name for label in pr.labels]
        backport_labels = [label for label in labels if backport_label_pattern.match(label)]
        if promoted_label not in labels:
            continue
        if not backport_labels:
            continue
        commits = get_pr_commits(repo, pr, stable_branch, start_commit)
        logging.info(f"Found PR #{pr.number} with commit {commits} and the following labels: {backport_labels}")
        for backport_label in backport_labels:
            version = backport_label.replace('backport/', '')
            backport_base_branch = backport_label.replace('backport/', backport_branch)
            backport(repo, pr, version, commits, backport_base_branch)


if __name__ == "__main__":
    main()
