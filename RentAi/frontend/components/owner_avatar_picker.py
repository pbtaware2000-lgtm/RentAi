"""Inline Streamlit v2 Owner avatar and native file-picker component."""

import streamlit as st


_HTML = """
<section class="profile-header">
  <label class="avatar" title="Change profile photo" aria-label="Choose profile photo">
    <img class="photo" alt="Owner profile photo" hidden>
    <span class="initials"></span>
    <span class="camera" aria-hidden="true">&#128247;</span>
    <input class="file-input" type="file" accept="image/jpeg,image/png,image/webp,image/gif"
      aria-label="Choose profile photo">
  </label>
  <div class="identity">
    <div class="name"></div>
    <div class="email"></div>
    <span class="role"></span>
  </div>
</section>
"""

_CSS = """
* { box-sizing: border-box; }
.profile-header {
  display: flex; align-items: center; gap: 18px; min-height: 108px;
  padding: 18px 24px; border: 1px solid #e2e8f0; border-radius: 14px;
  background: linear-gradient(120deg,#f8fafc,#fff 68%);
  box-shadow: 0 3px 12px rgba(15,23,42,.05);
}
.avatar {
  position: relative; display: grid; place-items: center; flex: 0 0 68px;
  width: 68px; height: 68px; overflow: hidden; border: 3px solid #ff6b1a;
  border-radius: 50%; background: #10243a; color: #fff; font-size: 22px;
  font-weight: 800; cursor: pointer; transition: box-shadow .15s ease;
}
.avatar:hover, .avatar:focus-within { box-shadow: 0 0 0 4px rgba(255,107,26,.2); }
.avatar.disabled { cursor: default; }
.avatar.disabled:hover, .avatar.disabled:focus-within { box-shadow: none; }
.avatar.disabled .camera { display: none; }
.photo { width: 100%; height: 100%; object-fit: cover; object-position: center; }
.file-input { position: absolute; inset: 0; z-index: 2; width: 100%; height: 100%; opacity: 0; cursor: pointer; }
.file-input:disabled { cursor: default; }
.camera {
  position: absolute; right: -1px; bottom: -1px; display: grid; place-items: center;
  width: 23px; height: 23px; border: 2px solid #fff; border-radius: 50%;
  background: #ff6b1a; color: #fff; font-size: 12px; pointer-events: none;
}
.name { color: #10243a; font-size: 22px; font-weight: 800; line-height: 1.2; }
.email { margin-top: 5px; color: #526176; font-size: 13px; overflow-wrap: anywhere; }
.role { display: inline-block; margin-top: 9px; padding: 4px 10px; border-radius: 999px;
  background: #fff1e8; color: #b9470a; font-size: 11px; font-weight: 800; letter-spacing: .04em; }
@media (max-width: 640px) {
  .profile-header { min-height: 94px; padding: 16px; gap: 13px; }
  .avatar { flex-basis: 56px; width: 56px; height: 56px; }
}
"""

_JS = """
export default function(component) {
  const { data = {}, parentElement, setTriggerValue } = component;
  const root = parentElement;
  const photo = root.querySelector('.photo');
  const initials = root.querySelector('.initials');
  const input = root.querySelector('.file-input');
  const avatar = root.querySelector('.avatar');

  root.querySelector('.name').textContent = data.name || 'Owner';
  root.querySelector('.email').textContent = data.email || 'Email unavailable';
  root.querySelector('.role').textContent = `${data.role || 'Owner'} Account`;
  initials.textContent = data.initials || 'O';
  photo.hidden = !data.photo_url;
  if (data.photo_url) photo.src = data.photo_url;
  initials.hidden = Boolean(data.photo_url);
  input.disabled = Boolean(data.disabled);
  avatar.classList.toggle('disabled', input.disabled);

  input.onchange = () => {
    const file = input.files && input.files[0];
    if (!file) return;
    if (file.size > Number(data.max_bytes || 0)) {
      setTriggerValue('photo_selected', { error: 'size' });
      input.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setTriggerValue('photo_selected', {
        name: file.name,
        type: file.type,
        data: String(reader.result).split(',', 2)[1] || '',
      });
      input.value = '';
    };
    reader.onerror = () => setTriggerValue('photo_selected', { error: 'read' });
    reader.readAsDataURL(file);
  };
}
"""

_AVATAR_COMPONENT = None


def owner_avatar_picker(*, name: str, email: str, role: str, initials: str,
                        photo_url: str | None, max_bytes: int, key: str,
                        disabled: bool = False):
    """Mount a circular avatar with a file input directly over the image."""
    global _AVATAR_COMPONENT
    if _AVATAR_COMPONENT is None:
        _AVATAR_COMPONENT = st.components.v2.component(
            "rentai_owner_avatar_picker",
            html=_HTML,
            css=_CSS,
            js=_JS,
        )
    result = _AVATAR_COMPONENT(
        data={
            "name": name,
            "email": email,
            "role": role,
            "initials": initials,
            "photo_url": photo_url or "",
            "max_bytes": max_bytes,
            "disabled": disabled,
        },
        on_photo_selected_change=lambda: None,
        key=key,
        height=146,
    )
    return result.photo_selected
