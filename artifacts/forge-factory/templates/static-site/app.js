const menu = document.querySelector('.menu-button');
const nav = document.querySelector('#site-nav');
if (menu && nav) menu.addEventListener('click', () => { const open = nav.classList.toggle('open'); menu.setAttribute('aria-expanded', String(open)); menu.textContent = open ? 'Close' : 'Menu'; });
document.querySelectorAll('#year').forEach((node) => { node.textContent = new Date().getFullYear(); });
const form = document.querySelector('#contact-form');
if (form) form.addEventListener('submit', (event) => { event.preventDefault(); const status = document.querySelector('#form-status'); if (!form.checkValidity()) { status.textContent = 'Please complete the required fields.'; form.reportValidity(); return; } status.textContent = 'This private starter does not send data yet. Configure a governed contact workflow before launch.'; });
