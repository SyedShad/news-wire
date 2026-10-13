"""Local image briefs. Provider dispatch is intentionally unavailable."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from html import escape


def now():
    return datetime.now(UTC).isoformat()


def fingerprint(story):
    fields = {key: story.get(key) for key in (
        'id', 'headline', 'summary', 'story_revision', 'material_revision', 'status'
    )}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class ImageBriefs:
    def __init__(self, database):
        self.database = database

    def prepare(self, story_id, headline, category, branding):
        story = self.database.one('SELECT * FROM story_cluster WHERE id = ?', (story_id,))
        if not story:
            raise LookupError('Story not found')
        if story['status'] in {'archived', 'withdrawn'}:
            raise ValueError('Archived or withdrawn stories cannot prepare images')
        values = []
        for name, value, limit in [('headline', headline, 160), ('category', category, 40), ('branding', branding, 60)]:
            value = str(value or '').strip()
            if not value or len(value) > limit or any(ord(c) < 32 for c in value):
                raise ValueError(f'{name.title()} must contain 1–{limit} printable characters')
            values.append(value)
        brief_id = secrets.token_hex(16)
        brief = dict(id=brief_id, story_id=story_id, story_fingerprint=fingerprint(story),
                     headline=values[0], category=values[1], branding=values[2],
                     width=1080, height=1440, status='awaiting_approval', created_at=now(),
                     provider='unavailable', generated_asset=None,
                     background_prompt=f'Editorial technology-news cover illustration about: {story["headline"]}. '
                     'Vertical 3:4, one central subject in upper two-thirds, vivid controlled background. '
                     'Lower third simple and dark for overlay. No text, watermark, publisher logo, '
                     'or invented factual details. Illustration, not documentary evidence.')
        self.database.execute('INSERT INTO app_state(key, value, updated_at) VALUES (?, ?, ?)',
                              ('image-brief:' + brief_id, json.dumps(brief), now()))
        return brief

    def get(self, brief_id):
        row = self.database.one('SELECT value FROM app_state WHERE key = ?', ('image-brief:' + brief_id,))
        if not row:
            raise LookupError('Image brief not found')
        return json.loads(row['value'])

    def approve(self, brief_id, confirmed):
        if confirmed != 'yes':
            raise ValueError('Confirm approval for this image brief')
        connection = self.database.connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT value FROM app_state WHERE key = ?', ('image-brief:' + brief_id,)).fetchone()
            if not row:
                raise LookupError('Image brief not found')
            brief = json.loads(row['value'])
            story = connection.execute('SELECT * FROM story_cluster WHERE id = ?', (brief['story_id'],)).fetchone()
            if not story or fingerprint(dict(story)) != brief['story_fingerprint']:
                raise ValueError('Story changed. Prepare and review a new image brief')
            if brief['status'] == 'awaiting_approval':
                brief.update(status='blocked_provider_unavailable', approved_at=now())
                connection.execute('UPDATE app_state SET value = ?, updated_at = ? WHERE key = ?',
                                   (json.dumps(brief), now(), 'image-brief:' + brief_id))
            connection.commit()
            return brief
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def layout_preview(brief):
    """Self-contained HTML wireframe; no network, generated art, or publisher logo."""
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Illustrative layout preview</title>
<style>body{margin:0;background:#222;color:white;font-family:Arial,sans-serif}aside{padding:16px;text-align:center}.cover{position:relative;width:540px;height:720px;margin:auto;overflow:hidden;background:linear-gradient(135deg,#65e6df,#545baa 55%,#a88cdc)}.hero{padding:80px 40px;text-align:center;font-size:24px;color:#11203c}.fade{position:absolute;inset:35% 0 0;background:linear-gradient(transparent,#000 55%)}.copy{position:absolute;bottom:36px;left:28px;right:28px;text-align:center}.category{display:inline-block;padding:5px 12px;border-radius:14px;background:#f06b70;color:#000;font-size:12px}h1{font-family:Impact,'Arial Narrow',sans-serif;text-transform:uppercase;font-size:43px;line-height:1;margin:14px 0;overflow-wrap:anywhere}h1 span{color:#65e6df}.brand{color:#f3f57a;font-size:14px}</style>
<aside>Layout preview · placeholder background · no generated image · 1080 × 1440 master</aside>
<div class="cover"><div class="hero">ILLUSTRATIVE PLACEHOLDER<br>No factual scene depicted</div><div class="fade"></div><div class="copy"><span class="category">''' + escape(brief['category']) + '''</span><h1><span>''' + escape(brief['headline']) + '''</span></h1><div class="brand">''' + escape(brief['branding']) + '''</div></div></div></html>'''
