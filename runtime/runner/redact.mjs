const sensitiveKey = /^(authorization|proxy-authorization|cookie|set-cookie|password|passwd|secret|token|access_token|refresh_token|client_secret|storageState)$/i;
export function secretValues(input) {
  const values = Object.values(input.environment?.secretVariables || {});
  for (const role of input.environment?.roles || []) {
    values.push(...Object.values(role.headers || {}));
    for (const cookie of role.storageState?.cookies || []) values.push(cookie.value);
    for (const origin of role.storageState?.origins || []) for (const item of origin.localStorage || []) values.push(item.value);
  }
  return [...new Set(values.filter(v => typeof v === 'string' && v.length > 0))].sort((a,b) => b.length-a.length);
}
export function makeRedactor(input) {
  const secrets = secretValues(input);
  const text = value => {
    let result = String(value);
    for (const value of secrets) {
      result = result.split(value).join('[REDACTED]');
      const encoded = encodeURIComponent(value);
      if (encoded !== value) result = result.split(encoded).join('[REDACTED]');
    }
    return result.replace(/\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+/gi, '$1: [REDACTED]')
      .replace(/([?&](?:token|secret|password|access_token|refresh_token)=)[^&#\s]*/gi, '$1[REDACTED]');
  };
  const object = value => {
    if (typeof value === 'string') return text(value);
    if (Array.isArray(value)) return value.map(object);
    if (!value || typeof value !== 'object') return value;
    if (typeof value.name === 'string' && sensitiveKey.test(value.name) && 'value' in value) return {...value, value:'[REDACTED]'};
    return Object.fromEntries(Object.entries(value).map(([key, val]) => [key,sensitiveKey.test(key) ? '[REDACTED]' : object(val)]));
  };
  return {text, object};
}
