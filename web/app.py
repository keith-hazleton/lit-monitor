"""
Flask web UI for editing Literature Monitor configuration.

Run with: python -m web.app
Or: flask --app web.app run
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import yaml

from src.config_loader import load_config, Config
from src.database import PaperDatabase

WEB_DIR = Path(__file__).parent
app = Flask(
    __name__,
    template_folder=WEB_DIR / 'templates',
    static_folder=WEB_DIR / 'static',
)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-in-production')

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
DATA_PATH = PROJECT_ROOT / "data" / "papers.db"


def load_config_raw() -> dict:
    """Load raw YAML config as dict."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config_raw(config: dict):
    """Save config dict to YAML file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


@app.route('/')
def index():
    """Main config editor page."""
    config = load_config_raw()

    # Get database stats
    stats = {}
    try:
        db = PaperDatabase(DATA_PATH)
        stats = db.get_stats()
    except Exception as e:
        stats = {'error': str(e)}

    return render_template('index.html', config=config, stats=stats)


@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current config as JSON."""
    return jsonify(load_config_raw())


@app.route('/api/config', methods=['POST'])
def update_config():
    """Update entire config from JSON."""
    try:
        new_config = request.json
        save_config_raw(new_config)
        return jsonify({'status': 'ok', 'message': 'Config saved'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/queries', methods=['POST'])
def update_queries():
    """Update search queries."""
    try:
        config = load_config_raw()
        queries = request.json.get('queries', [])
        # Filter empty strings
        config['search_queries'] = [q.strip() for q in queries if q.strip()]
        save_config_raw(config)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/authors', methods=['POST'])
def update_authors():
    """Update watched authors."""
    try:
        config = load_config_raw()
        authors = request.json.get('authors', [])
        config['watched_authors'] = [a.strip() for a in authors if a.strip()]
        save_config_raw(config)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/projects', methods=['POST'])
def update_projects():
    """Update active projects."""
    try:
        config = load_config_raw()
        projects = request.json.get('projects', [])
        config['active_projects'] = projects
        save_config_raw(config)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/journals', methods=['POST'])
def update_journals():
    """Update journal weights."""
    try:
        config = load_config_raw()
        journals = request.json.get('journal_weights', {})
        config['journal_weights'] = journals
        save_config_raw(config)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update general settings."""
    try:
        config = load_config_raw()
        settings = request.json.get('settings', {})
        config['settings'] = settings
        save_config_raw(config)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/stats')
def get_stats():
    """Get database statistics."""
    try:
        db = PaperDatabase(DATA_PATH)
        stats = db.get_stats()
        runs = db.get_search_runs(limit=5)
        return jsonify({
            'stats': stats,
            'recent_runs': [
                {
                    'date': r.run_date,
                    'papers_found': r.papers_found,
                    'new_papers': r.new_papers,
                }
                for r in runs
            ]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/papers')
def papers():
    """Paper feedback page."""
    db = PaperDatabase(DATA_PATH)

    # Get filter params
    feedback_filter = request.args.get('filter', 'all')
    days = int(request.args.get('days', 30))

    # Get papers based on filter
    if feedback_filter == 'starred':
        paper_list = db.get_starred_papers(limit=200)
    elif feedback_filter == 'dismissed':
        paper_list = db.get_dismissed_papers(limit=200)
    else:
        paper_list = db.get_recent_papers(days=days)
        if feedback_filter == 'none':
            paper_list = [p for p in paper_list if p._user_feedback is None]

    # Sort by relevance score (descending), unranked at end
    paper_list.sort(key=lambda p: p.relevance_score if p.relevance_score is not None else -1, reverse=True)

    stats = db.get_feedback_stats()

    return render_template('papers.html',
                         papers=paper_list,
                         stats=stats,
                         filter=feedback_filter,
                         days=days)


@app.route('/api/feedback', methods=['POST'])
def set_feedback():
    """Set feedback on a paper."""
    try:
        data = request.json
        paper_id = data.get('paper_id')
        feedback = data.get('feedback')  # 'star', 'dismiss', or None

        if not paper_id:
            return jsonify({'status': 'error', 'message': 'paper_id required'}), 400

        if feedback and feedback not in ('star', 'dismiss'):
            return jsonify({'status': 'error', 'message': 'feedback must be star, dismiss, or null'}), 400

        db = PaperDatabase(DATA_PATH)
        db.set_feedback(paper_id, feedback)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/seeds')
def seeds():
    """Seed papers page."""
    db = PaperDatabase(DATA_PATH)
    seed_list = db.get_seed_papers()
    return render_template('seeds.html', seeds=seed_list)


@app.route('/api/seeds/add', methods=['POST'])
def add_seed():
    """Add a seed paper by DOI or PMID."""
    try:
        data = request.json
        identifier = data.get('identifier', '').strip()

        if not identifier:
            return jsonify({'status': 'error', 'message': 'identifier required'}), 400

        from src.paper_lookup import lookup_paper
        paper, source = lookup_paper(identifier)

        if not paper:
            return jsonify({'status': 'error', 'message': f'Could not find paper for: {identifier}'}), 404

        db = PaperDatabase(DATA_PATH)
        is_new = db.insert_seed_paper(paper, source=source)

        return jsonify({
            'status': 'ok',
            'title': paper.title,
            'is_new': is_new,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/suggestions')
def suggestions():
    """Config suggestions page."""
    db = PaperDatabase(DATA_PATH)
    pending = db.get_pending_suggestions()
    all_suggestions = db.get_all_suggestions()
    resolved = [s for s in all_suggestions if s.status != 'pending']
    return render_template('suggestions.html', pending=pending, resolved=resolved)


@app.route('/api/suggestions/resolve', methods=['POST'])
def resolve_suggestion():
    """Accept or dismiss a config suggestion."""
    try:
        data = request.json
        suggestion_id = data.get('id')
        status = data.get('status')

        if not suggestion_id or status not in ('accepted', 'dismissed'):
            return jsonify({'status': 'error', 'message': 'id and status (accepted/dismissed) required'}), 400

        db = PaperDatabase(DATA_PATH)

        if status == 'accepted':
            # Get the suggestion to auto-apply
            suggestions = db.get_pending_suggestions()
            suggestion = next((s for s in suggestions if s.id == suggestion_id), None)
            if suggestion and suggestion.suggestion_data:
                _apply_suggestion(suggestion)

        db.resolve_suggestion(suggestion_id, status)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


def _apply_suggestion(suggestion):
    """Auto-apply a suggestion to config.yaml."""
    config = load_config_raw()
    data = suggestion.suggestion_data

    if suggestion.suggestion_type == 'search_query' and data.get('query'):
        queries = config.get('search_queries', [])
        if data['query'] not in queries:
            queries.append(data['query'])
            config['search_queries'] = queries

    elif suggestion.suggestion_type == 'project_keyword' and data.get('project') and data.get('keyword'):
        for project in config.get('active_projects', []):
            if project.get('name') == data['project']:
                keywords = project.get('keywords', [])
                if data['keyword'] not in keywords:
                    keywords.append(data['keyword'])
                    project['keywords'] = keywords
                break

    elif suggestion.suggestion_type == 'watched_author' and data.get('author'):
        authors = config.get('watched_authors', [])
        if data['author'] not in authors:
            authors.append(data['author'])
            config['watched_authors'] = authors

    elif suggestion.suggestion_type == 'new_project' and data.get('name'):
        projects = config.get('active_projects', [])
        if not any(p.get('name') == data['name'] for p in projects):
            projects.append({
                'name': data['name'],
                'keywords': data.get('keywords', []),
            })
            config['active_projects'] = projects

    save_config_raw(config)


@app.route('/api/test-config')
def test_config():
    """Test if current config is valid."""
    try:
        config = load_config(CONFIG_PATH)
        return jsonify({
            'status': 'ok',
            'queries': len(config.search_queries),
            'authors': len(config.watched_authors),
            'projects': len(config.active_projects),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


if __name__ == '__main__':
    print(f"Config file: {CONFIG_PATH}")
    print(f"Database: {DATA_PATH}")
    print(f"\nStarting web UI at http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
