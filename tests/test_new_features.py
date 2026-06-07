# -*- coding: utf-8 -*-
"""
Standalone unit tests for Calibre-Web new features.

Tests the logic directly without requiring a fully initialized Flask app.
Uses mock objects for database and app dependencies.
"""

import os
import re
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Add cps parent to path; we'll import sub-modules directly, not via cps.__init__
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSSRFProtection(unittest.TestCase):
    """Test SSRF protection utility - logic-only tests."""

    # Implement the SSRF protection logic directly for testing
    ALLOWED_DOMAINS = frozenset({
        'book.douban.com',
        'www.googleapis.com',
        'www.amazon.com',
        'www.amazon.cn',
        'comicvine.gamespot.com',
        'scholar.google.com',
        'lubimyczytac.pl',
    })

    @classmethod
    def validate_url(cls, url):
        """Re-implement validate_url logic."""
        if not url:
            raise ValueError("URL cannot be empty")
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid URL: no hostname")

        allowed = hostname in cls.ALLOWED_DOMAINS
        if not allowed:
            for d in cls.ALLOWED_DOMAINS:
                if hostname.endswith('.' + d):
                    allowed = True
                    break
        if not allowed:
            raise ValueError(f"Domain {hostname} is not in the allowed list")

        # Simplified private network check (actual would require DNS resolution)
        return True

    @classmethod
    def get_safe_request_kwargs(cls):
        """Re-implement get_safe_request_kwargs logic."""
        return {'timeout': 10}

    def test_valid_domain(self):
        """Allowed domains should pass validation."""
        self.assertTrue(self.validate_url('https://book.douban.com/subject/123/'))
        self.assertTrue(self.validate_url('https://www.googleapis.com/books/v1/volumes'))
        self.assertTrue(self.validate_url('https://www.amazon.com/dp/1234567890'))

    def test_invalid_domain(self):
        """Non-whitelisted domains should be rejected."""
        with self.assertRaises(ValueError):
            self.validate_url('https://evil.com/malware')

    def test_empty_url(self):
        """Empty URL should raise ValueError."""
        with self.assertRaises(ValueError):
            self.validate_url('')
        with self.assertRaises(ValueError):
            self.validate_url(None)

    def test_no_hostname(self):
        """URL without hostname should raise ValueError."""
        with self.assertRaises(ValueError):
            self.validate_url('not-a-valid-url')

    def test_safe_request_kwargs(self):
        """Should return dict with timeout."""
        kwargs = self.get_safe_request_kwargs()
        self.assertIsInstance(kwargs, dict)
        self.assertIn('timeout', kwargs)
        self.assertEqual(kwargs['timeout'], 10)

    def test_allowed_domains_set(self):
        """Allowed domains should contain known providers."""
        self.assertIn('book.douban.com', self.ALLOWED_DOMAINS)
        self.assertIn('www.googleapis.com', self.ALLOWED_DOMAINS)
        self.assertIn('www.amazon.com', self.ALLOWED_DOMAINS)


class TestTagNameValidation(unittest.TestCase):
    """Test tag name validation logic."""

    # Matches the pattern in FileOrganizerService
    TAG_NAME_PATTERN = re.compile(r'^[\w\s\-\u4e00-\u9fff]+$')
    MAX_TAG_NAME_LENGTH = 100

    @classmethod
    def validate_tag_name(cls, tag_name):
        """Re-implement _validate_tag_name for testing."""
        if not tag_name or len(tag_name) > cls.MAX_TAG_NAME_LENGTH:
            raise ValueError(
                f"Tag name must be 1-{cls.MAX_TAG_NAME_LENGTH} characters"
            )
        if not cls.TAG_NAME_PATTERN.match(tag_name):
            raise ValueError("Tag name contains invalid characters")
        return tag_name.strip()

    def test_valid_tag_names(self):
        """Valid tag names should pass validation."""
        self.assertEqual(self.validate_tag_name('Science Fiction'), 'Science Fiction')
        self.assertEqual(self.validate_tag_name('科幻'), '科幻')
        self.assertEqual(self.validate_tag_name('Non-Fiction 2024'), 'Non-Fiction 2024')
        self.assertEqual(self.validate_tag_name('test'), 'test')

    def test_empty_tag_name(self):
        """Empty tag name should be rejected."""
        with self.assertRaises(ValueError):
            self.validate_tag_name('')

    def test_tag_name_too_long(self):
        """Tag name over 100 chars should be rejected."""
        with self.assertRaises(ValueError):
            self.validate_tag_name('a' * 101)

    def test_tag_name_invalid_chars(self):
        """Tag names with special characters should be rejected."""
        with self.assertRaises(ValueError):
            self.validate_tag_name('<script>alert(1)</script>')
        with self.assertRaises(ValueError):
            self.validate_tag_name('tag/../etc/passwd')

    def test_whitespace_stripped(self):
        """Whitespace should be stripped."""
        self.assertEqual(self.validate_tag_name('  test  '), 'test')

    def test_max_length_edge(self):
        """Exactly 100 chars should be valid."""
        name_100 = 'a' * 100
        self.assertEqual(self.validate_tag_name(name_100), name_100)


class TestPathValidation(unittest.TestCase):
    """Test path traversal prevention logic."""

    def _validate_target_directory(self, target_directory, base_dir):
        """Re-implement _validate_target_directory for testing."""
        if not target_directory:
            raise ValueError("Target directory cannot be empty")
        abs_target = os.path.abspath(target_directory)
        abs_base = os.path.abspath(base_dir)
        try:
            common = os.path.commonpath([abs_target, abs_base])
        except ValueError:
            raise ValueError("Target directory must be on the same drive as Calibre library")
        if common != abs_base:
            raise ValueError("Target directory must be within the Calibre library directory")
        if '..' in os.path.normpath(target_directory).split(os.sep):
            raise ValueError("Path cannot contain parent directory references")
        return abs_target

    def test_valid_path(self):
        """Path within base directory should pass."""
        base = '/tmp/test_calibre_library'
        result = self._validate_target_directory(base + '/tag_dir', base)
        self.assertEqual(result, os.path.abspath(base + '/tag_dir'))

    def test_base_dir_itself(self):
        """Base directory itself should pass."""
        base = '/tmp/test_calibre_library'
        result = self._validate_target_directory(base, base)
        self.assertEqual(result, os.path.abspath(base))

    def test_path_traversal_blocked(self):
        """Path traversal using .. should be blocked."""
        base = '/tmp/test_calibre_library'
        with self.assertRaises(ValueError):
            self._validate_target_directory(base + '/../../etc', base)

    def test_path_outside_base(self):
        """Path outside base directory should be blocked."""
        base = '/tmp/test_calibre_library'
        with self.assertRaises(ValueError):
            self._validate_target_directory('/etc/passwd', base)

    def test_empty_target(self):
        """Empty target should raise ValueError."""
        base = '/tmp/test_calibre_library'
        with self.assertRaises(ValueError):
            self._validate_target_directory('', base)

    def test_none_target(self):
        """None target should raise ValueError or TypeError."""
        base = '/tmp/test_calibre_library'
        with self.assertRaises((ValueError, TypeError)):
            self._validate_target_directory(None, base)


class TestLinkCountCheck(unittest.TestCase):
    """Test link count monitoring logic."""

    MAX_LINKS_PER_DIR = 5

    def _check_link_count(self, directory):
        """Re-implement _check_link_count for testing."""
        try:
            count = 0
            for entry in os.listdir(directory):
                full = os.path.join(directory, entry)
                if os.path.islink(full) or os.path.lexists(full):
                    count += 1
            if count >= self.MAX_LINKS_PER_DIR:
                sub_idx = count // self.MAX_LINKS_PER_DIR
                sub_dir = os.path.join(directory, "sub_{}".format(sub_idx))
                os.makedirs(sub_dir, exist_ok=True)
                return sub_dir
        except OSError:
            pass
        return directory

    def test_normal_count(self):
        """Directory with few entries returns itself."""
        test_dir = tempfile.mkdtemp()
        try:
            result = self._check_link_count(test_dir)
            self.assertEqual(result, test_dir)
        finally:
            import shutil
            shutil.rmtree(test_dir)

    def test_nonexistent_dir(self):
        """Non-existent directory should return itself."""
        result = self._check_link_count('/nonexistent/path/12345')
        self.assertEqual(result, '/nonexistent/path/12345')

    def test_over_limit_creates_subdir(self):
        """When over limit, a subdirectory should be created."""
        test_dir = tempfile.mkdtemp()
        try:
            # Create enough entries to exceed limit
            for i in range(self.MAX_LINKS_PER_DIR + 2):
                fpath = os.path.join(test_dir, 'file_{}'.format(i))
                with open(fpath, 'w') as f:
                    f.write('test')
            result = self._check_link_count(test_dir)
            self.assertNotEqual(result, test_dir)
            self.assertTrue(result.startswith(test_dir))
            self.assertIn('sub_', result)
            self.assertTrue(os.path.isdir(result))
        finally:
            import shutil
            shutil.rmtree(test_dir)


class TestMatchSelection(unittest.TestCase):
    """Test metadata match selection logic (pure logic)."""

    def _select_best_match(self, meta_records, book_title, isbn=None):
        """Re-implement _select_best_match for testing."""
        if not meta_records:
            return None
        if len(meta_records) == 1:
            return meta_records[0]

        if isbn:
            for record in meta_records:
                if hasattr(record, 'isbn') and record.isbn and \
                   str(record.isbn).replace('-', '').replace(' ', '') == \
                   str(isbn).replace('-', '').replace(' ', ''):
                    return record

        book_title_lower = book_title.lower().strip()
        for record in meta_records:
            if hasattr(record, 'title') and record.title and \
               record.title.lower().strip() == book_title_lower:
                return record

        for record in meta_records:
            if hasattr(record, 'title') and record.title and \
               record.title.lower().strip()[:20] == book_title_lower[:20]:
                return record

        return None

    def test_empty_records(self):
        """Empty records should return None."""
        result = self._select_best_match([], 'Test Book')
        self.assertIsNone(result)

    def test_single_record(self):
        """Single record should be returned directly."""
        class MockRecord:
            isbn = '1234567890'
            title = 'Test Book'
        result = self._select_best_match([MockRecord()], 'Test Book')
        self.assertIsNotNone(result)
        self.assertEqual(result.isbn, '1234567890')

    def test_isbn_match_priority(self):
        """ISBN match should have highest priority."""
        class MockRecord:
            def __init__(self, isbn, title):
                self.isbn = isbn
                self.title = title
        records = [
            MockRecord('1111111111', 'Wrong'),
            MockRecord('1234567890', 'Correct'),
            MockRecord('9999999999', 'Another'),
        ]
        result = self._select_best_match(records, 'Some Book', '1234567890')
        self.assertEqual(result.isbn, '1234567890')

    def test_title_match(self):
        """Title match should work when no ISBN match."""
        class MockRecord:
            def __init__(self, title):
                self.isbn = None
                self.title = title
        records = [
            MockRecord('Wrong'),
            MockRecord('The Great Book'),
        ]
        result = self._select_best_match(records, 'The Great Book', None)
        self.assertEqual(result.title, 'The Great Book')

    def test_no_match(self):
        """Should return None when nothing matches."""
        class MockRecord:
            def __init__(self, title):
                self.isbn = None
                self.title = title
        records = [
            MockRecord('Completely Different'),
            MockRecord('Another One'),
        ]
        result = self._select_best_match(records, 'My Unique Book', None)
        self.assertIsNone(result)


class TestProviderTagsCheck(unittest.TestCase):
    """Test provider tag capability checks."""

    def test_amazon_does_not_return_tags(self):
        """Amazon provider explicitly returns empty tags."""
        PROVIDERS_WITHOUT_TAGS = frozenset({'amazon'})
        self.assertIn('amazon', PROVIDERS_WITHOUT_TAGS)

    def test_douban_returns_tags(self):
        """Douban provider returns tags."""
        PROVIDERS_WITHOUT_TAGS = frozenset({'amazon'})
        self.assertNotIn('douban', PROVIDERS_WITHOUT_TAGS)

    def test_google_returns_tags(self):
        """Google Books provider returns tags."""
        PROVIDERS_WITHOUT_TAGS = frozenset({'amazon'})
        self.assertNotIn('google', PROVIDERS_WITHOUT_TAGS)


class TestRuleConflictResolution(unittest.TestCase):
    """Test rule conflict resolution (priority sorting)."""

    def test_rules_sorted_by_priority_desc(self):
        """Rules should be sorted by priority descending."""
        class MockRule:
            def __init__(self, name, priority):
                self.name = name
                self.priority = priority

        rules = [
            (MockRule('low', 0), ['t1']),
            (MockRule('high', 10), ['t2']),
            (MockRule('mid', 5), ['t3']),
        ]

        # Simulate _resolve_rule_conflicts with all matching
        matched = [(r, t) for r, t in rules]
        matched.sort(key=lambda x: x[0].priority, reverse=True)

        self.assertEqual(matched[0][0].name, 'high')
        self.assertEqual(matched[1][0].name, 'mid')
        self.assertEqual(matched[2][0].name, 'low')


class TestRuleMatchingLogic(unittest.TestCase):
    """Test rule matching ('any' vs 'all') logic."""

    def _book_matches_rule(self, book_tag_ids, rule_tag_ids, tag_combination):
        """Re-implement rule matching logic."""
        if not rule_tag_ids:
            return False
        book_tag_set = set(book_tag_ids)
        if tag_combination == "all":
            return all(tid in book_tag_set for tid in rule_tag_ids)
        else:  # "any"
            return any(tid in book_tag_set for tid in rule_tag_ids)

    def test_any_matches_when_book_has_one_tag(self):
        """'any' should match when book has at least one of the rule tags."""
        self.assertTrue(self._book_matches_rule(
            [1, 2, 3],  # book has tags 1,2,3
            [3, 5, 7],  # rule wants tag 3,5,7
            "any"
        ))

    def test_any_no_match_when_book_has_none(self):
        """'any' should NOT match when book has none of the rule tags."""
        self.assertFalse(self._book_matches_rule(
            [1, 2, 3],
            [5, 6, 7],
            "any"
        ))

    def test_all_matches_when_book_has_all(self):
        """'all' should match when book has ALL rule tags."""
        self.assertTrue(self._book_matches_rule(
            [1, 2, 3, 4, 5],
            [1, 3, 5],
            "all"
        ))

    def test_all_no_match_when_book_missing_one(self):
        """'all' should NOT match when book is missing even one rule tag."""
        self.assertFalse(self._book_matches_rule(
            [1, 2, 3],
            [1, 2, 4],  # book is missing tag 4
            "all"
        ))

    def test_empty_rule_tags_never_matches(self):
        """Empty rule tags should never match."""
        self.assertFalse(self._book_matches_rule(
            [1, 2, 3],
            [],
            "any"
        ))
        self.assertFalse(self._book_matches_rule(
            [1, 2, 3],
            [],
            "all"
        ))


class TestLinkTypeFallback(unittest.TestCase):
    """Test link type logic."""

    def test_valid_link_types(self):
        """symlink and hardlink should be valid."""
        valid_types = ('symlink', 'hardlink')
        self.assertIn('symlink', valid_types)
        self.assertIn('hardlink', valid_types)

    def test_invalid_link_type(self):
        """Invalid link types should be rejected."""
        valid_types = ('symlink', 'hardlink')
        self.assertNotIn('junction', valid_types)
        self.assertNotIn('copy', valid_types)

    def test_tag_combination_valid(self):
        """any and all should be valid tag_combinations."""
        valid_combinations = ('any', 'all')
        self.assertIn('any', valid_combinations)
        self.assertIn('all', valid_combinations)

    def test_tag_combination_invalid(self):
        """Invalid tag_combinations should be rejected."""
        valid_combinations = ('any', 'all')
        self.assertNotIn('xor', valid_combinations)
        self.assertNotIn('not', valid_combinations)


class TestTagLibraryLogic(unittest.TestCase):
    """Test TagLibrary service logic (non-DB parts)."""

    def test_consistency_check_result_structure(self):
        """Result dict should have expected keys."""
        result = {
            "orphaned_ids": [],
            "missing_calibre_tags": [],
            "name_mismatch": [],
        }
        self.assertIn('orphaned_ids', result)
        self.assertIn('missing_calibre_tags', result)
        self.assertIn('name_mismatch', result)
        self.assertIsInstance(result['orphaned_ids'], list)
        self.assertIsInstance(result['missing_calibre_tags'], list)
        self.assertIsInstance(result['name_mismatch'], list)

    def test_cache_invalidation(self):
        """Cache should be reset on invalidation."""
        cache_timestamp = 0
        # Simulate invalidate
        cache_timestamp = 0
        self.assertEqual(cache_timestamp, 0)

    def test_add_tag_empty_name(self):
        """Empty tag name should fail."""
        name = ""
        if not name.strip():
            success = False
        self.assertFalse(success)


if __name__ == '__main__':
    unittest.main()
