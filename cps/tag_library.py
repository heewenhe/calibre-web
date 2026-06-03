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
Tag Library Management - Web Controller.

Provides blueprint for the tag library management interface:
- Tag list, add, edit, delete operations
- Tag synchronization with Calibre Tags
- Tag merge and batch categorization
"""

from flask import Blueprint, request, make_response, jsonify
from flask_babel import gettext as _

from cps.cw_login import current_user
from cps.usermanagement import user_login_required
from cps.admin import admin_required
from cps import logger
from cps.render_template import render_title_template
from cps.services.TagLibrary import TagLibraryService

tag_library = Blueprint('tag_library', __name__)
log = logger.create()


@tag_library.route('/admin/tag_library', methods=['GET'])
@user_login_required
@admin_required
def tag_library_page():
    """Tag library management page."""
    return render_title_template(
        'admin_tag_library.html',
        title=_("Tag Library"),
        page="taglibrary"
    )


@tag_library.route('/api/tag_library/tags', methods=['GET'])
@user_login_required
@admin_required
def api_get_tags():
    """Get tag library tags with optional filtering."""
    svc = TagLibraryService()
    category = request.args.get('category', default=None)
    active_only = request.args.get('active_only', '1') == '1'

    tags = svc.get_all_tags(category=category, active_only=active_only)

    result = []
    for tag in tags:
        result.append({
            'id': tag.id,
            'name': tag.name,
            'calibre_tag_id': tag.calibre_tag_id,
            'category': tag.category,
            'description': tag.description,
            'usage_count': tag.usage_count,
            'is_active': tag.is_active,
            'created_at': tag.created_at.isoformat() if tag.created_at else None,
            'updated_at': tag.updated_at.isoformat() if tag.updated_at else None,
        })
    return make_response(jsonify({'tags': result, 'count': len(result)}))


@tag_library.route('/api/tag_library/tags', methods=['POST'])
@user_login_required
@admin_required
def api_add_tag():
    """Add a new tag."""
    data = request.get_json(silent=True)
    if not data:
        return make_response(jsonify({'success': False, 'error': 'Invalid request data'}), 400)

    name = data.get('name', '').strip()
    category = data.get('category', '')
    description = data.get('description', '')

    svc = TagLibraryService()
    success, tag_id, error = svc.add_tag(name, category, description)

    if success:
        return make_response(jsonify({'success': True, 'tag_id': tag_id}))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@tag_library.route('/api/tag_library/tags/<int:tag_id>', methods=['PUT'])
@user_login_required
@admin_required
def api_update_tag(tag_id):
    """Update a tag."""
    data = request.get_json(silent=True)
    if not data:
        return make_response(jsonify({'success': False, 'error': 'Invalid request data'}), 400)

    svc = TagLibraryService()
    success, error = svc.update_tag(
        tag_id,
        name=data.get('name'),
        category=data.get('category'),
        description=data.get('description'),
    )

    if success:
        return make_response(jsonify({'success': True}))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@tag_library.route('/api/tag_library/tags/<int:tag_id>', methods=['DELETE'])
@user_login_required
@admin_required
def api_delete_tag(tag_id):
    """Delete a tag."""
    svc = TagLibraryService()
    success, error = svc.delete_tag(tag_id)

    if success:
        return make_response(jsonify({'success': True}))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@tag_library.route('/api/tag_library/merge', methods=['POST'])
@user_login_required
@admin_required
def api_merge_tags():
    """Merge tags."""
    data = request.get_json(silent=True)
    if not data:
        return make_response(jsonify({'success': False, 'error': 'Invalid request data'}), 400)

    source_ids = data.get('source_tag_ids', [])
    target_name = data.get('target_tag_name', '')

    if not source_ids or not target_name:
        return make_response(jsonify({
            'success': False,
            'error': 'source_tag_ids and target_tag_name are required'
        }), 400)

    svc = TagLibraryService()
    success, merged_count, error = svc.merge_tags(source_ids, target_name)

    if success:
        return make_response(jsonify({
            'success': True,
            'merged_count': merged_count
        }))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@tag_library.route('/api/tag_library/categorize', methods=['POST'])
@user_login_required
@admin_required
def api_categorize_tags():
    """Batch-set category for tags."""
    data = request.get_json(silent=True)
    if not data:
        return make_response(jsonify({'success': False, 'error': 'Invalid request data'}), 400)

    tag_ids = data.get('tag_ids', [])
    category = data.get('category', '')

    if not tag_ids:
        return make_response(jsonify({
            'success': False,
            'error': 'tag_ids is required'
        }), 400)

    svc = TagLibraryService()
    success, updated_count, error = svc.categorize_tags(tag_ids, category)

    if success:
        return make_response(jsonify({
            'success': True,
            'updated_count': updated_count
        }))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@tag_library.route('/api/tag_library/sync', methods=['POST'])
@user_login_required
@admin_required
def api_sync_from_calibre():
    """Sync TagLibrary from Calibre Tags."""
    svc = TagLibraryService()
    success, added_count, error = svc.sync_from_calibre()

    if success:
        return make_response(jsonify({
            'success': True,
            'added_count': added_count
        }))
    return make_response(jsonify({'success': False, 'error': error}), 400)


@tag_library.route('/api/tag_library/consistency_check', methods=['GET'])
@user_login_required
@admin_required
def api_consistency_check():
    """Check consistency between TagLibrary and Calibre Tags."""
    svc = TagLibraryService()
    result = svc.sync_consistency_check()
    return make_response(jsonify({'success': True, 'result': result}))


@tag_library.route('/api/tag_library/calibre_tags', methods=['GET'])
@user_login_required
@admin_required
def api_get_calibre_tags():
    """Get tags directly from Calibre's Tags table."""
    svc = TagLibraryService()
    tags = svc.get_calibre_tags()
    result = [{'id': t.id, 'name': t.name} for t in tags]
    return make_response(jsonify({'tags': result, 'count': len(result)}))


@tag_library.route('/api/tag_library/search', methods=['GET'])
@user_login_required
@admin_required
def api_search_tags():
    """Search tags by keyword."""
    keyword = request.args.get('q', '')
    category = request.args.get('category', None)

    if not keyword:
        return make_response(jsonify({'tags': [], 'count': 0}))

    svc = TagLibraryService()
    tags = svc.search_tags(keyword, category)

    result = []
    for tag in tags:
        result.append({
            'id': tag.id,
            'name': tag.name,
            'category': tag.category,
            'usage_count': tag.usage_count,
        })
    return make_response(jsonify({'tags': result, 'count': len(result)}))