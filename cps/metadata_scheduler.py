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
Metadata Scan Scheduler - Web Controller.

Provides blueprint for the metadata scan scheduling interface:
- Scan provider selection
- Book range selection (all, by tag, by author)
- Scan history browsing
"""

from flask import Blueprint, request, make_response, jsonify
from flask_babel import gettext as _

from cps.cw_login import current_user
from cps.usermanagement import user_login_required
from cps.admin import admin_required
from cps import logger
from cps.render_template import render_title_template
from cps.services.worker import WorkerThread
from cps.tasks.metadata_scan import TaskMetadataScan

metadata_scheduler = Blueprint('metadata_scheduler', __name__)
log = logger.create()


@metadata_scheduler.route('/admin/metadata_scan', methods=['GET'])
@user_login_required
@admin_required
def metadata_scan_page():
    """Metadata scan management page."""
    return render_title_template(
        'admin_metadata_scan.html',
        title=_("Metadata Scan"),
        page="metadatascan"
    )


@metadata_scheduler.route('/api/metadata_scan/start', methods=['POST'])
@user_login_required
@admin_required
def api_start_scan():
    """Start a metadata scan task.

    Request JSON:
        provider_id: str - Metadata provider ID (e.g. "douban")
        book_ids: list[int] or None - Book IDs to scan, None for all
        max_retries: int - Max retry attempts (default 3)
    """
    data = request.get_json(silent=True)
    if not data:
        return make_response(jsonify({'success': False, 'error': 'Invalid request data'}), 400)

    provider_id = data.get('provider_id')
    if not provider_id:
        return make_response(jsonify({
            'success': False,
            'error': 'provider_id is required'
        }), 400)

    book_ids = data.get('book_ids', None)
    max_retries = data.get('max_retries', 3)

    # Check for Amazon (no tags)
    if provider_id.lower() == 'amazon':
        return make_response(jsonify({
            'success': False,
            'error': (
                "Amazon provider does not return tags. "
                "Please choose a provider like Douban or Google Books."
            )
        }), 400)

    user_id = current_user.id if not current_user.is_anonymous else None

    task = TaskMetadataScan(
        provider_id=provider_id,
        book_ids=book_ids,
        user_id=user_id,
        max_retries=max_retries,
    )
    WorkerThread.add(current_user, task)

    return make_response(jsonify({
        'success': True,
        'task_id': str(task.id),
        'message': 'Metadata scan task started for provider: {}'.format(provider_id)
    }))


@metadata_scheduler.route('/api/metadata_scan/history', methods=['GET'])
@user_login_required
@admin_required
def api_get_scan_history():
    """Get scan history records."""
    from cps import ub

    try:
        limit = request.args.get('limit', default=20, type=int)
        offset = request.args.get('offset', default=0, type=int)

        q = ub.session.query(ub.ScanHistory).order_by(
            ub.ScanHistory.started_at.desc()
        )

        total = q.count()
        records = q.limit(limit).offset(offset).all()

        result = []
        for r in records:
            result.append({
                'id': r.id,
                'provider': r.provider,
                'total_books': r.total_books,
                'processed_books': r.processed_books,
                'tags_added': r.tags_added,
                'tags_skipped': r.tags_skipped,
                'status': r.status,
                'started_at': r.started_at.isoformat() if r.started_at else None,
                'finished_at': r.finished_at.isoformat() if r.finished_at else None,
                'error_log': r.error_log,
            })

        return make_response(jsonify({
            'success': True,
            'records': result,
            'total': total,
        }))
    except Exception as e:
        log.error("Failed to get scan history: %s", str(e))
        return make_response(jsonify({
            'success': False,
            'error': str(e)
        }), 500)


@metadata_scheduler.route('/api/metadata_scan/providers', methods=['GET'])
@user_login_required
@admin_required
def api_get_providers():
    """Get available metadata providers with tag support info."""
    try:
        from cps.services.Metadata import Metadata
        metadata = Metadata()
        providers = metadata.get_source_prefs()

        result = []
        for p in providers:
            result.append({
                'id': p.get('id', ''),
                'name': p.get('name', ''),
                'url': p.get('url', ''),
                'supports_tags': p.get('id', '').lower() != 'amazon',
            })

        return make_response(jsonify({
            'success': True,
            'providers': result
        }))
    except Exception as e:
        log.error("Failed to get providers: %s", str(e))
        return make_response(jsonify({
            'success': False,
            'error': str(e)
        }), 500)