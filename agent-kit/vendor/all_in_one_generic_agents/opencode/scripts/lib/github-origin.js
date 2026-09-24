'use strict';

function normalizeGitHubGitOrigin(value) {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().replace(/\.git$/i, '').replace(/\/+$/, '');
  const match = normalized.match(
    /^(?:<url>\/)([^/]+\/[^/]+)$/i
  );
  return match ? match[1].toLowerCase() : null;
}

module.exports = {
  normalizeGitHubGitOrigin,
};
