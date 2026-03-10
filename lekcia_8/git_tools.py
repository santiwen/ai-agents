"""
git_tools.py
Git integrácia - nástroje pre LangGraph agenta.
GitPython API pre história, diff, blame, search.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

try:
    import git
    from git import Repo, InvalidGitRepositoryError
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    print("[git_tools] WARN: gitpython nie je nainštalovaný. pip install gitpython")


@dataclass
class GitCommit:
    sha: str
    author: str
    date: str
    message: str
    files_changed: List[str]


@dataclass
class FileDiff:
    filepath: str
    old_content: str
    new_content: str
    patch: str
    additions: int
    deletions: int


class GitTools:
    """
    Git nástroje pre AI asistenta.
    Všetky operácie sú read-only okrem propose_change (ktoré vyžaduje schválenie).
    """

    def __init__(self, repo_path: str = '.'):
        self.repo_path = Path(repo_path).resolve()

        if not GIT_AVAILABLE:
            self.repo = None
            return

        try:
            self.repo = Repo(str(self.repo_path), search_parent_directories=True)
            self.repo_root = Path(self.repo.working_dir)
            print(f"[git] Repozitár: {self.repo_root}")
        except InvalidGitRepositoryError:
            print(f"[git] WARN: {repo_path} nie je git repozitár")
            self.repo = None
            self.repo_root = self.repo_path

    def get_file_content(self, filepath: str, ref: str = 'HEAD') -> str:
        """
        Vráti obsah súboru z git histórie.
        ref môže byť: 'HEAD', commit SHA, branch name, tag.
        """
        if not self.repo:
            # Fallback: čítaj priamo
            full_path = self.repo_path / filepath
            if full_path.exists():
                with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                    return f.read()
            return ""

        try:
            blob = self.repo.commit(ref).tree[filepath.replace('\\', '/')]
            return blob.data_stream.read().decode('utf-8', errors='replace')
        except (KeyError, Exception) as e:
            # Skúsi relatívnu cestu od repo rootu
            try:
                rel = str(Path(filepath).relative_to(self.repo_root))
                blob = self.repo.commit(ref).tree[rel.replace('\\', '/')]
                return blob.data_stream.read().decode('utf-8', errors='replace')
            except Exception:
                # Posledný fallback: subprocess
                result = subprocess.run(
                    ['git', 'show', f'{ref}:{filepath}'],
                    capture_output=True, text=True,
                    cwd=str(self.repo_root)
                )
                return result.stdout if result.returncode == 0 else ""

    def get_git_history(self, filepath: str, max_commits: int = 10) -> List[GitCommit]:
        """
        Git história súboru (--follow pre tracking renames).
        """
        if not self.repo:
            return []

        try:
            commits = []
            for commit in self.repo.iter_commits(
                paths=filepath.replace('\\', '/'),
                max_count=max_commits
            ):
                # Súbory zmenené v tomto commite
                changed = []
                if commit.parents:
                    diff = commit.diff(commit.parents[0])
                    changed = [d.a_path for d in diff]

                commits.append(GitCommit(
                    sha=commit.hexsha[:12],
                    author=f"{commit.author.name} <{commit.author.email}>",
                    date=commit.committed_datetime.strftime('%Y-%m-%d %H:%M'),
                    message=commit.message.strip(),
                    files_changed=changed,
                ))
            return commits
        except Exception as e:
            # Fallback: subprocess git log
            return self._git_log_subprocess(filepath, max_commits)

    def _git_log_subprocess(self, filepath: str, max_commits: int) -> List[GitCommit]:
        """Fallback git log cez subprocess."""
        result = subprocess.run(
            ['git', 'log', '--follow', f'-{max_commits}',
             '--format=%H|%an|%ae|%ad|%s', '--date=short', '--', filepath],
            capture_output=True, text=True,
            cwd=str(self.repo_root)
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split('|', 4)
            if len(parts) >= 5:
                commits.append(GitCommit(
                    sha=parts[0][:12],
                    author=f"{parts[1]} <{parts[2]}>",
                    date=parts[3],
                    message=parts[4],
                    files_changed=[],
                ))
        return commits

    def get_blame(self, filepath: str, line_start: int = None,
                   line_end: int = None) -> List[Dict]:
        """
        Git blame pre súbor alebo rozsah riadkov.
        """
        result = subprocess.run(
            ['git', 'blame', '--porcelain', filepath],
            capture_output=True, text=True,
            cwd=str(self.repo_root)
        )
        if result.returncode != 0:
            return []

        blame_entries = []
        lines = result.stdout.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r'^[0-9a-f]{40}', line):
                parts = line.split()
                sha = parts[0][:12]
                orig_line = int(parts[1])
                curr_line = int(parts[2])

                entry = {'sha': sha, 'line': curr_line, 'author': '', 'date': '', 'content': ''}
                i += 1
                while i < len(lines) and not re.match(r'^[0-9a-f]{40}', lines[i]):
                    if lines[i].startswith('author '):
                        entry['author'] = lines[i][7:]
                    elif lines[i].startswith('author-time '):
                        from datetime import datetime
                        ts = int(lines[i][12:])
                        entry['date'] = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                    elif lines[i].startswith('\t'):
                        entry['content'] = lines[i][1:]
                    i += 1

                if line_start and curr_line < line_start:
                    continue
                if line_end and curr_line > line_end:
                    break

                blame_entries.append(entry)
            else:
                i += 1

        return blame_entries

    def search_in_git(self, pattern: str, file_pattern: str = None,
                       case_sensitive: bool = False) -> List[Dict]:
        """
        Fulltext search v aktuálnom stave repozitára cez git grep.
        """
        cmd = ['git', 'grep', '-n', '--heading']
        if not case_sensitive:
            cmd.append('-i')
        cmd.extend(['-e', pattern])
        if file_pattern:
            cmd.extend(['--', file_pattern])

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(self.repo_root)
        )

        results = []
        current_file = None
        for line in result.stdout.splitlines():
            if ':' not in line:
                current_file = line
                continue
            parts = line.split(':', 2)
            if len(parts) >= 2 and current_file:
                try:
                    lineno = int(parts[0])
                    content = parts[1] if len(parts) > 1 else ''
                    results.append({
                        'file': current_file,
                        'line': lineno,
                        'content': content.strip(),
                    })
                except ValueError:
                    pass

        return results[:200]  # limit

    def get_diff(self, filepath: str, ref1: str = 'HEAD~1',
                  ref2: str = 'HEAD') -> FileDiff:
        """
        Diff medzi dvoma commitmi pre daný súbor.
        """
        result = subprocess.run(
            ['git', 'diff', ref1, ref2, '--', filepath],
            capture_output=True, text=True,
            cwd=str(self.repo_root)
        )

        patch = result.stdout
        additions = patch.count('\n+') - patch.count('\n+++')
        deletions = patch.count('\n-') - patch.count('\n---')

        old_content = self.get_file_content(filepath, ref1)
        new_content = self.get_file_content(filepath, ref2)

        return FileDiff(
            filepath=filepath,
            old_content=old_content,
            new_content=new_content,
            patch=patch,
            additions=additions,
            deletions=deletions,
        )

    def get_changed_files(self, ref: str = 'HEAD') -> List[str]:
        """Vráti súbory zmenené v danom commite."""
        result = subprocess.run(
            ['git', 'diff-tree', '--no-commit-id', '-r', '--name-only', ref],
            capture_output=True, text=True,
            cwd=str(self.repo_root)
        )
        return result.stdout.strip().splitlines()

    def get_recent_changes(self, days: int = 7) -> List[Dict]:
        """Súbory zmenené za posledných N dní."""
        result = subprocess.run(
            ['git', 'log', f'--since={days} days ago',
             '--format=%H|%ad|%s', '--date=short', '--name-only'],
            capture_output=True, text=True,
            cwd=str(self.repo_root)
        )

        changes = []
        current_commit = None
        for line in result.stdout.splitlines():
            if '|' in line and len(line.split('|')) >= 3:
                parts = line.split('|', 2)
                current_commit = {
                    'sha': parts[0][:12],
                    'date': parts[1],
                    'message': parts[2],
                    'files': [],
                }
                changes.append(current_commit)
            elif line.strip() and current_commit and not line.startswith(' '):
                current_commit['files'].append(line.strip())

        return changes

    def propose_changeset(self, changes: List[Dict[str, str]],
                           reason: str) -> Dict:
        """
        Navrhuje multi-file zmenu. Každá zmena = {filepath, new_content, reason}.
        Vráti kombinovaný diff, NEPÍŠE bez schválenia.
        """
        proposals = []
        combined_patch = []
        total_adds = 0
        total_dels = 0

        for change in changes:
            filepath = change['filepath']
            new_content = change['new_content']
            file_reason = change.get('reason', reason)

            proposal = self.propose_change(filepath, new_content, file_reason)
            proposals.append(proposal)
            if proposal['patch']:
                combined_patch.append(f"--- {filepath} ---")
                combined_patch.append(proposal['patch'])
            total_adds += proposal['additions']
            total_dels += proposal['deletions']

        return {
            'status': 'pending_approval',
            'reason': reason,
            'files': [p['filepath'] for p in proposals],
            'proposals': proposals,
            'patch': '\n'.join(combined_patch),
            'additions': total_adds,
            'deletions': total_dels,
        }

    def apply_changeset(self, changeset: Dict, approved: bool) -> Dict:
        """
        Atomický apply multi-file changeset.
        Všetko alebo nič - ak jeden súbor zlyhá, rollback všetkých.
        """
        if not approved:
            return {'status': 'rejected', 'files': changeset.get('files', [])}

        applied = []
        backups = {}

        try:
            for proposal in changeset['proposals']:
                filepath = proposal['filepath']
                full_path = self.repo_root / filepath

                # Backup
                if full_path.exists():
                    with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                        backups[filepath] = f.read()

                # Apply
                result = self.apply_change(proposal, approved=True)
                if result.get('status') != 'applied':
                    raise RuntimeError(f"Zlyhalo pre {filepath}: {result}")
                applied.append(filepath)

            return {
                'status': 'applied',
                'files': applied,
                'backups': {fp: str(self.repo_root / fp) + '.bak' for fp in backups},
            }

        except Exception as e:
            # Rollback - obnov všetky už zmenené súbory
            for filepath, original in backups.items():
                if filepath in applied:
                    full_path = self.repo_root / filepath
                    with open(full_path, 'w', encoding='utf-8') as f:
                        f.write(original)
            return {
                'status': 'rollback',
                'error': str(e),
                'rolled_back': [fp for fp in applied if fp in backups],
            }

    def propose_change(self, filepath: str, new_content: str,
                        reason: str) -> Dict:
        """
        Navrhuje zmenu súboru - generuje diff, NEPÍŠE bez schválenia.
        Vráti patch a čaká na human approval.
        """
        current_content = ""
        full_path = self.repo_root / filepath
        if full_path.exists():
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                current_content = f.read()

        # Vytvor unified diff
        import difflib
        diff_lines = list(difflib.unified_diff(
            current_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{filepath}",
            tofile=f"b/{filepath}",
            lineterm='',
        ))
        patch = ''.join(diff_lines)

        additions = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))

        return {
            'status': 'pending_approval',
            'filepath': filepath,
            'reason': reason,
            'patch': patch,
            'additions': additions,
            'deletions': deletions,
            'new_content': new_content,
            'current_content': current_content,
        }

    def apply_change(self, proposal: Dict, approved: bool,
                      commit_msg: str = None) -> Dict:
        """
        Aplikuje navrhnutú zmenu po schválení.
        NIKDY sa nespustí automaticky - vyžaduje explicit approved=True.
        """
        if not approved:
            return {'status': 'rejected', 'filepath': proposal['filepath']}

        filepath = proposal['filepath']
        new_content = proposal['new_content']
        full_path = self.repo_root / filepath

        # Backup
        backup_path = str(full_path) + '.bak'
        if full_path.exists():
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(proposal['current_content'])

        # Zapis
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        result = {
            'status': 'applied',
            'filepath': filepath,
            'backup': backup_path,
        }

        # Git add (bez commit - to nechaj na užívateľa)
        if self.repo:
            try:
                self.repo.index.add([str(full_path)])
                result['staged'] = True

                if commit_msg:
                    self.repo.index.commit(commit_msg)
                    result['committed'] = True
            except Exception as e:
                result['git_error'] = str(e)

        return result
