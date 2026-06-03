# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
File Organizer - Web Controller.

Provides blueprint for the file organization management interface:
- Rule CRUD operations
- Rule preview (which books match)
- Apply rules to books
"""

from flask import Blueprint, request, make_response, jsonify
from flask_babel import gettext as _

from cps.cw_login import current_user
from cps.usermanagement import user_login_required
from cps.admin import admin_required
from cps import logger, config
from cps.render_template import render_title_template
from cps.services.FileOrganizer import FileOrganizerService
from cps.services.worker import WorkerThread
from cps.tasks.file_organize import TaskFileOrganize

file_organizer = Blueprint('file_organizer', __name__)
log = logger.create()


@file_organizer.route('/admin/file_organizer', methods=['GET'])
@user_login_required
@admin_required
def file_organizer_page():
    """File organization management page."""
    # Check Google Drive mode
    gdrive_mode = getattr(config, 'config_use_google_drive', False)
    return render_title_template(
        'admin_file_organizer.html',
        title=_("File Organizer"),
        page="fileorganizer",
        gdrive_mode=gdrive_mode
    )


# ── Rules API ──────────────────────────────────────────────────────────

@file_organizer.route('/api/file_organizer/rules', methods=['GET'])
@user_login_required
@admin_required
def api_get_rules():
    """Get all file organization rules."""
    svc = FileOrganizerService()
    rules = svc.get_rules(active_only=False)

    result = []
    for rule, tag_names in rules:
        result.append({
            'id': rule.id,
            'name': rule.name,
            'tag_names': tag_names,
            'tag_combination': rule.tag_combination,
            'target_directory': rule.target_directory,
            'link_type': rule.link_type,
            'priority': rule.priority,
            'is_active': rule.is_active,
            'created_at': rule.created_at.isoformat() if rule.created_at else None,
        })
    return make_response(jsonify({'rules': result, 'count': len(result)}))


@file_organizer.route('/api/file_organizer/rules', methods=['POST'])
@user_login_required
@admin_required
def api_add_rule():
    """Add a new file organization rule."""
    data = request.get_json(silent=True)
    if not data:
        return make_response(jsonify({'success': False, 'error': 'Invalid request data'}), 400)

    svc = FileOrganizerService()
    success, rule_id, error = svc.add_rule(
        name=data.get('name', ''),
        tag_names=data.get('tag_names', []),
        tag_combination=data.get('tag_combination', 'any'),
        target_directory=data.get('target_directory'),
        priority=data.get('priority', 0),
        link_type=data.get('link_type', 'symlink'),
    )

    if success:
        return make_response(jsonify({'success': True, 'rule_id': rule_id}))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@file_organizer.route('/api/file_organizer/rules/<int:rule_id>', methods=['PUT'])
@user_login_required
@admin_required
def api_update_rule(rule_id):
    """Update a file organization rule."""
    data = request.get_json(silent=True)
    if not data:
        return make_response(jsonify({'success': False, 'error': 'Invalid request data'}), 400)

    svc = FileOrganizerService()

    update_kwargs = {}
    for field in ['name', 'tag_names', 'tag_combination', 'target_directory',
                  'priority', 'link_type', 'is_active']:
        if field in data:
            update_kwargs[field] = data[field]

    success, error = svc.update_rule(rule_id, **update_kwargs)

    if success:
        return make_response(jsonify({'success': True}))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@file_organizer.route('/api/file_organizer/rules/<int:rule_id>', methods=['DELETE'])
@user_login_required
@admin_required
def api_delete_rule(rule_id):
    """Delete a file organization rule."""
    svc = FileOrganizerService()
    success, error = svc.delete_rule(rule_id)

    if success:
        return make_response(jsonify({'success': True}))
    return make_response(jsonify({'success': False, 'error': error}), 400)


# ── Actions API ────────────────────────────────────────────────────────

@file_organizer.route('/api/file_organizer/preview', methods=['POST'])
@user_login_required
@admin_required
def api_preview_rules():
    """Preview which books would be organized by rules."""
    data = request.get_json(silent=True) or {}
    rule_id = data.get('rule_id', None)

    svc = FileOrganizerService()
    preview = svc.get_preview(rule_id=rule_id)

    summary = {
        rule_name: {
            'book_ids': book_ids,
            'count': len(book_ids)
        }
        for rule_name, book_ids in preview.items()
    }

    return make_response(jsonify({'success': True, 'preview': summary}))


@file_organizer.route('/api/file_organizer/apply', methods=['POST'])
@user_login_required
@admin_required
def api_apply_rules():
    """Start a file organization task."""
    # Check Google Drive mode
    if getattr(config, 'config_use_google_drive', False):
        return make_response(jsonify({
            'success': False,
            'error': 'File organization is not available in Google Drive mode. '
                     'Symlinks are not supported by Google Drive.'
        }), 400)

    data = request.get_json(silent=True) or {}

    rule_ids = data.get('rule_ids', None)
    book_ids = data.get('book_ids', None)
    user_id = current_user.id if not current_user.is_anonymous else None

    task = TaskFileOrganize(
        rule_ids=rule_ids,
        book_ids=book_ids,
        user_id=user_id,
    )
    WorkerThread.add(current_user, task)

    return make_response(jsonify({
        'success': True,
        'task_id': str(task.id),
        'message': 'File organization task started'
    }))


@file_organizer.route('/api/file_organizer/clean_link/<int:rule_id>', methods=['POST'])
@user_login_required
@admin_required
def api_clean_stale_links(rule_id):
    """Clean stale links for a specific rule's target directory."""
    svc = FileOrganizerService()
    rule, _ = svc.get_rule_by_id(rule_id)

    if not rule:
        return make_response(jsonify({
            'success': False,
            'error': 'Rule not found'
        }), 404)

    cleaned, errors = svc.clean_stale_links(rule.target_directory)

    return make_response(jsonify({
        'success': True,
        'cleaned_count': cleaned,
        'errors': errors
    }))


@file_organizer.route('/api/file_organizer/apply_single/<int:book_id>', methods=['POST'])
@user_login_required
@admin_required
def api_apply_rules_to_single_book(book_id):
    """Apply all active rules to a single book."""
    svc = FileOrganizerService()
    success, created, errors = svc.apply_rules_to_book(book_id)

    return make_response(jsonify({
        'success': success,
        'links_created': created,
        'errors': errors
    }))