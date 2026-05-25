import React, { useState } from 'react';
import StreamPlayer from './components/StreamPlayer';
import AdminPage from './components/AdminPage';

const TAB_STYLE = (active) => ({
  padding: '6px 18px',
  cursor: 'pointer',
  border: 'none',
  borderBottom: active ? '2px solid #fff' : '2px solid transparent',
  background: 'transparent',
  color: active ? '#fff' : '#666',
  fontSize: 13,
  fontFamily: 'monospace',
  fontWeight: active ? 700 : 400,
});

function App() {
  const [tab, setTab] = useState('stream');

  return (
    <div style={{ background: '#141414', minHeight: '100vh' }}>
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #2a2a2a', padding: '0 24px' }}>
        <button style={TAB_STYLE(tab === 'stream')} onClick={() => setTab('stream')}>
          스트리밍
        </button>
        <button style={TAB_STYLE(tab === 'admin')} onClick={() => setTab('admin')}>
          관리자
        </button>
      </div>
      {tab === 'stream' ? <StreamPlayer streamId="demo" /> : <AdminPage />}
    </div>
  );
}

export default App;
