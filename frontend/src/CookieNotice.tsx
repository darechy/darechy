import { useState } from 'react';

const noticeKey = 'darechy.storage-notice.v1';

export default function CookieNotice({ be, hidden }: { be: boolean; hidden: boolean }) {
  const [visible, setVisible] = useState(() => {
    try {
      return localStorage.getItem(noticeKey) !== 'seen';
    } catch {
      return true;
    }
  });

  if (hidden || !visible) return null;

  function dismiss() {
    try {
      localStorage.setItem(noticeKey, 'seen');
    } catch {
      // The banner can still be dismissed when browser storage is unavailable.
    }
    setVisible(false);
  }

  return (
    <aside className="storage-notice" aria-labelledby="storage-notice-title">
      <h2 id="storage-notice-title">{be ? 'Cookies і даныя браўзера' : 'Cookies и данные браузера'}</h2>
      <p>{be
        ? 'ДаРэчы захоўвае мову і гісторыю чатаў у гэтым браўзеры. Рэкламныя і аналітычныя cookies не выкарыстоўваюцца.'
        : 'ДаРэчы сохраняет язык и историю чатов в этом браузере. Рекламные и аналитические cookies не используются.'}</p>
      <button type="button" className="btn btn-primary" onClick={dismiss}>
        {be ? 'Зразумела' : 'Понятно'}
      </button>
    </aside>
  );
}
