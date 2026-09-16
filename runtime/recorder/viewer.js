import RFB from './novnc/core/rfb.js';

const status = document.getElementById('status');
const base = new URL('.', window.location.href);
base.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
base.pathname += 'websockify';
base.search = '';
const rfb = new RFB(document.getElementById('screen'), base.href);
rfb.scaleViewport = true;
rfb.resizeSession = false;
rfb.addEventListener('connect', () => { status.textContent = '已连接 · 在 Inspector 选择断言，再点选页面目标'; });
rfb.addEventListener('disconnect', () => { status.textContent = '录制桌面连接已断开'; });
rfb.addEventListener('securityfailure', () => { status.textContent = '录制桌面认证失败'; });
document.getElementById('focus').onclick = () => rfb.focus();
