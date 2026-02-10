/**
 * Cloudflare Worker for one-click Zotero paper import + Capacities integration + Feedback
 *
 * Uses HMAC-signed URLs to prevent unauthorized requests.
 *
 * Endpoints:
 *   GET /add?data=<base64>&ts=<timestamp>&sig=<signature> - Add paper to Zotero & Capacities
 *   GET /feedback?data=<base64>&ts=<timestamp>&sig=<signature>&action=star|dismiss - Record feedback
 *   GET /feedback/pending?key=<api_key> - Get pending feedback entries (Pi polls this)
 *   POST /feedback/ack - Acknowledge processed feedback entries
 *   GET /health - Health check
 *
 * Required secrets (set via `wrangler secret put`):
 *   ZOTERO_API_KEY - Your Zotero API key
 *   ZOTERO_USER_ID - Your Zotero user ID
 *   SIGNING_SECRET - Secret for HMAC signatures (generate with: openssl rand -hex 32)
 *
 * Optional secrets:
 *   CAPACITIES_API_TOKEN - Your Capacities API token
 *   CAPACITIES_SPACE_ID - Your Capacities space ID
 *   FEEDBACK_API_KEY - API key for feedback sync (shared with Pi's .env)
 *
 * KV Namespaces:
 *   FEEDBACK_KV - Stores pending feedback entries
 */

const ZOTERO_API_BASE = 'https://api.zotero.org';
const CAPACITIES_API_BASE = 'https://api.capacities.io';
const URL_EXPIRY_HOURS = 168; // Links valid for 7 days

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS headers for browser requests
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      if (path === '/health') {
        return jsonResponse({ status: 'ok', timestamp: Date.now() }, corsHeaders);
      }

      if (path === '/add') {
        return await handleAddPaper(url, env, corsHeaders);
      }

      if (path === '/feedback' && request.method === 'GET') {
        return await handleFeedback(url, env, corsHeaders);
      }

      if (path === '/feedback/pending' && request.method === 'GET') {
        return await handleFeedbackPending(url, env, corsHeaders);
      }

      if (path === '/feedback/ack' && request.method === 'POST') {
        return await handleFeedbackAck(request, env, corsHeaders);
      }

      // Default: show usage info
      return htmlResponse(renderHomePage(), corsHeaders);

    } catch (error) {
      console.error('Worker error:', error);
      return htmlResponse(renderErrorPage(error.message), corsHeaders, 500);
    }
  },
};

/**
 * Handle adding a paper to Zotero
 */
async function handleAddPaper(url, env, corsHeaders) {
  // Check for required secrets
  if (!env.ZOTERO_API_KEY || !env.ZOTERO_USER_ID) {
    throw new Error('Zotero credentials not configured');
  }
  if (!env.SIGNING_SECRET) {
    throw new Error('Signing secret not configured');
  }

  // Get parameters
  const encodedData = url.searchParams.get('data');
  const timestamp = url.searchParams.get('ts');
  const signature = url.searchParams.get('sig');

  if (!encodedData || !timestamp || !signature) {
    throw new Error('Missing required parameters (data, ts, sig)');
  }

  // Verify timestamp (prevent replay attacks)
  const ts = parseInt(timestamp, 10);
  const now = Date.now();
  const age = now - ts;
  const maxAge = URL_EXPIRY_HOURS * 60 * 60 * 1000;

  if (isNaN(ts) || age < 0 || age > maxAge) {
    throw new Error('Link has expired. Please generate a new digest.');
  }

  // Verify HMAC signature
  const expectedSig = await generateSignature(encodedData, timestamp, env.SIGNING_SECRET);
  if (signature !== expectedSig) {
    throw new Error('Invalid signature. Link may have been tampered with.');
  }

  // Decode paper metadata
  let paper;
  try {
    const decoded = atob(encodedData.replace(/-/g, '+').replace(/_/g, '/'));
    paper = JSON.parse(decoded);
  } catch (e) {
    throw new Error('Invalid paper data encoding');
  }

  // Validate required fields
  if (!paper.title) {
    throw new Error('Paper title is required');
  }

  // Create Zotero item
  const zoteroItem = createZoteroItem(paper);

  // Send to Zotero API
  const response = await fetch(
    `${ZOTERO_API_BASE}/users/${env.ZOTERO_USER_ID}/items`,
    {
      method: 'POST',
      headers: {
        'Zotero-API-Key': env.ZOTERO_API_KEY,
        'Zotero-API-Version': '3',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify([zoteroItem]),
    }
  );

  if (!response.ok) {
    const errorText = await response.text();
    console.error('Zotero API error:', response.status, errorText);

    if (response.status === 403) {
      throw new Error('Zotero API key invalid or lacks write permission');
    }
    throw new Error(`Zotero API error: ${response.status}`);
  }

  const result = await response.json();

  // Get the created item key
  const itemKey = result.successful?.['0']?.key;
  const zoteroUrl = itemKey
    ? `https://www.zotero.org/users/${env.ZOTERO_USER_ID}/items/${itemKey}`
    : null;

  // Also add to Capacities if configured
  let capacitiesResult = null;
  if (env.CAPACITIES_API_TOKEN && env.CAPACITIES_SPACE_ID) {
    capacitiesResult = await addToCapacities(paper, env);
  }

  return htmlResponse(renderSuccessPage(paper.title, zoteroUrl, capacitiesResult), corsHeaders);
}

/**
 * Generate HMAC-SHA256 signature
 */
async function generateSignature(data, timestamp, secret) {
  const encoder = new TextEncoder();
  const message = `${data}.${timestamp}`;

  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );

  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(message));

  // Convert to hex string
  return Array.from(new Uint8Array(signature))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Convert paper metadata to Zotero item format
 */
function createZoteroItem(paper) {
  // Determine item type
  let itemType = 'journalArticle';
  if (paper.journal?.toLowerCase().includes('preprint') ||
      paper.journal?.toLowerCase().includes('rxiv')) {
    itemType = 'preprint';
  }

  // Format authors for Zotero
  const creators = (paper.authors || []).slice(0, 20).map(author => {
    // Handle "Last Initials" format (e.g., "Smith JK")
    const match = author.match(/^([^,]+?)\s+([A-Z]+)$/);
    if (match) {
      return {
        creatorType: 'author',
        lastName: match[1],
        firstName: match[2].split('').join('. ') + '.',
      };
    }

    // Handle "Last, First" format
    if (author.includes(',')) {
      const [lastName, firstName] = author.split(',').map(s => s.trim());
      return { creatorType: 'author', lastName, firstName };
    }

    // Fallback: try to split on last space
    const lastSpace = author.lastIndexOf(' ');
    if (lastSpace > 0) {
      return {
        creatorType: 'author',
        firstName: author.slice(0, lastSpace),
        lastName: author.slice(lastSpace + 1),
      };
    }

    return { creatorType: 'author', name: author };
  });

  // Build the item
  const item = {
    itemType,
    title: paper.title,
    creators,
    abstractNote: paper.abstract || '',
    date: paper.date || '',
    url: paper.url || '',
    DOI: paper.doi || '',
    publicationTitle: paper.journal || '',
    tags: [
      { tag: 'lit-monitor' },
    ],
  };

  return item;
}

/**
 * Add paper to Capacities via save-weblink API
 */
async function addToCapacities(paper, env) {
  try {
    // Build markdown content with paper details
    const mdContent = buildCapacitiesMarkdown(paper);

    // Use the paper URL (PubMed or DOI)
    const paperUrl = paper.doi
      ? `https://doi.org/${paper.doi}`
      : paper.url;

    const response = await fetch(`${CAPACITIES_API_BASE}/save-weblink`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.CAPACITIES_API_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        spaceId: env.CAPACITIES_SPACE_ID,
        url: paperUrl,
        titleOverwrite: paper.title,
        descriptionOverwrite: paper.abstract?.slice(0, 500) || '',
        mdText: mdContent,
        tags: ['lit-monitor', paper.journal?.toLowerCase().includes('rxiv') ? 'preprint' : 'journal-article'],
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('Capacities API error:', response.status, errorText);
      return { success: false, error: `Capacities error: ${response.status}` };
    }

    return { success: true };
  } catch (error) {
    console.error('Capacities error:', error);
    return { success: false, error: error.message };
  }
}

/**
 * Build markdown content for Capacities
 */
function buildCapacitiesMarkdown(paper) {
  let md = '';

  // Authors
  if (paper.authors?.length) {
    const authorStr = paper.authors.length > 5
      ? paper.authors.slice(0, 5).join(', ') + ' et al.'
      : paper.authors.join(', ');
    md += `**Authors:** ${authorStr}\n\n`;
  }

  // Journal and date
  if (paper.journal) {
    md += `**Journal:** ${paper.journal}`;
    if (paper.date) {
      md += ` (${paper.date})`;
    }
    md += '\n\n';
  }

  // DOI
  if (paper.doi) {
    md += `**DOI:** [${paper.doi}](https://doi.org/${paper.doi})\n\n`;
  }

  // Summary (if provided from Claude ranking)
  if (paper.summary) {
    md += `## Summary\n${paper.summary}\n\n`;
  }

  // Abstract
  if (paper.abstract) {
    md += `## Abstract\n${paper.abstract}\n`;
  }

  return md;
}

/**
 * Handle feedback (star/dismiss) from email links
 */
async function handleFeedback(url, env, corsHeaders) {
  if (!env.SIGNING_SECRET) {
    throw new Error('Signing secret not configured');
  }

  const encodedData = url.searchParams.get('data');
  const timestamp = url.searchParams.get('ts');
  const signature = url.searchParams.get('sig');
  const action = url.searchParams.get('action');

  if (!encodedData || !timestamp || !signature || !action) {
    throw new Error('Missing required parameters');
  }

  if (action !== 'star' && action !== 'dismiss') {
    throw new Error('Invalid action. Must be "star" or "dismiss".');
  }

  // Verify timestamp
  const ts = parseInt(timestamp, 10);
  const now = Date.now();
  const age = now - ts;
  const maxAge = URL_EXPIRY_HOURS * 60 * 60 * 1000;

  if (isNaN(ts) || age < 0 || age > maxAge) {
    throw new Error('Link has expired. Please generate a new digest.');
  }

  // Verify HMAC signature
  const expectedSig = await generateSignature(encodedData, timestamp, env.SIGNING_SECRET);
  if (signature !== expectedSig) {
    throw new Error('Invalid signature.');
  }

  // Decode paper data
  let paperData;
  try {
    const decoded = atob(encodedData.replace(/-/g, '+').replace(/_/g, '/'));
    paperData = JSON.parse(decoded);
  } catch (e) {
    throw new Error('Invalid data encoding');
  }

  const paperId = paperData.paper_id;
  const paperTitle = paperData.title || 'Unknown';

  if (!paperId) {
    throw new Error('Missing paper_id in data');
  }

  // Store in KV (if available)
  if (env.FEEDBACK_KV) {
    const kvKey = `feedback:${paperId}:${Date.now()}`;
    await env.FEEDBACK_KV.put(kvKey, JSON.stringify({
      paper_id: paperId,
      action: action,
      timestamp: Date.now(),
      title: paperTitle,
    }), { expirationTtl: 60 * 60 * 24 * 30 }); // 30 day TTL
  }

  return htmlResponse(renderFeedbackSuccessPage(paperTitle, action), corsHeaders);
}

/**
 * Return pending feedback entries for the Pi to sync
 */
async function handleFeedbackPending(url, env, corsHeaders) {
  // Authenticate with API key
  const apiKey = url.searchParams.get('key');
  if (!env.FEEDBACK_API_KEY || apiKey !== env.FEEDBACK_API_KEY) {
    return jsonResponse({ error: 'Unauthorized' }, corsHeaders, 401);
  }

  if (!env.FEEDBACK_KV) {
    return jsonResponse({ entries: [] }, corsHeaders);
  }

  // List all feedback keys
  const list = await env.FEEDBACK_KV.list({ prefix: 'feedback:' });
  const entries = [];

  for (const key of list.keys) {
    const value = await env.FEEDBACK_KV.get(key.name);
    if (value) {
      try {
        const entry = JSON.parse(value);
        entry.key = key.name;
        entries.push(entry);
      } catch (e) {
        // Skip malformed entries
      }
    }
  }

  return jsonResponse({ entries }, corsHeaders);
}

/**
 * Acknowledge (delete) processed feedback entries
 */
async function handleFeedbackAck(request, env, corsHeaders) {
  const body = await request.json();

  // Authenticate
  if (!env.FEEDBACK_API_KEY || body.key !== env.FEEDBACK_API_KEY) {
    return jsonResponse({ error: 'Unauthorized' }, corsHeaders, 401);
  }

  if (!env.FEEDBACK_KV) {
    return jsonResponse({ status: 'ok', deleted: 0 }, corsHeaders);
  }

  const keys = body.keys || [];
  let deleted = 0;

  for (const key of keys) {
    try {
      await env.FEEDBACK_KV.delete(key);
      deleted++;
    } catch (e) {
      // Ignore delete errors
    }
  }

  return jsonResponse({ status: 'ok', deleted }, corsHeaders);
}

function renderFeedbackSuccessPage(title, action) {
  const emoji = action === 'star' ? '⭐' : '👋';
  const verb = action === 'star' ? 'Starred' : 'Dismissed';
  const message = action === 'star'
    ? 'This paper will be used to improve future rankings.'
    : 'This paper will be deprioritized in future rankings.';

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${verb} Paper</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }
    .icon { font-size: 64px; margin-bottom: 10px; }
    h1 { color: #2c3e50; margin-top: 0; }
    .title { color: #666; font-style: italic; margin: 20px; padding: 15px; background: #f8f8f8; border-radius: 6px; }
    .message { color: #555; margin-top: 15px; }
  </style>
</head>
<body>
  <div class="icon">${emoji}</div>
  <h1>${verb}!</h1>
  <p class="title">${escapeHtml(title)}</p>
  <p class="message">${message}</p>
  <p><small>You can close this tab.</small></p>
</body>
</html>`;
}

// Helper functions
function jsonResponse(data, corsHeaders, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders },
  });
}

function htmlResponse(html, corsHeaders, status = 200) {
  return new Response(html, {
    status,
    headers: { 'Content-Type': 'text/html', ...corsHeaders },
  });
}

function renderHomePage() {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Literature Monitor - Zotero Worker</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
    h1 { color: #2c3e50; }
    code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
    .status { color: #27ae60; }
  </style>
</head>
<body>
  <h1>Literature Monitor - Zotero Worker</h1>
  <p class="status">✓ Worker is running</p>
  <p>This worker handles one-click paper imports to Zotero from the Literature Monitor digest.</p>
  <p>Links are cryptographically signed and expire after 7 days for security.</p>
</body>
</html>`;
}

function renderSuccessPage(title, zoteroUrl, capacitiesResult) {
  const viewLink = zoteroUrl
    ? `<p><a href="${zoteroUrl}" target="_blank" style="color: #3498db;">View in Zotero →</a></p>`
    : '';

  // Capacities status
  let capacitiesStatus = '';
  if (capacitiesResult) {
    if (capacitiesResult.success) {
      capacitiesStatus = '<p style="color: #27ae60;">✓ Also saved to Capacities</p>';
    } else {
      capacitiesStatus = `<p style="color: #e67e22;">⚠ Capacities: ${escapeHtml(capacitiesResult.error)}</p>`;
    }
  }

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Added to Zotero</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }
    .success { color: #27ae60; font-size: 64px; margin-bottom: 10px; }
    h1 { color: #2c3e50; margin-top: 0; }
    .title { color: #666; font-style: italic; margin: 20px; padding: 15px; background: #f8f8f8; border-radius: 6px; }
  </style>
</head>
<body>
  <div class="success">✓</div>
  <h1>Added to Zotero!</h1>
  <p class="title">${escapeHtml(title)}</p>
  ${viewLink}
  ${capacitiesStatus}
  <p><small>You can close this tab.</small></p>
</body>
</html>`;
}

function renderErrorPage(message) {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Error - Zotero Worker</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }
    .error { color: #e74c3c; font-size: 64px; margin-bottom: 10px; }
    h1 { color: #2c3e50; margin-top: 0; }
    .message { color: #c0392b; background: #fdf2f2; padding: 15px; border-radius: 6px; margin: 20px 0; }
  </style>
</head>
<body>
  <div class="error">✗</div>
  <h1>Could not add to Zotero</h1>
  <p class="message">${escapeHtml(message)}</p>
</body>
</html>`;
}

function escapeHtml(text) {
  if (!text) return '';
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
