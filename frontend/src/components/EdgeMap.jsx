import React from 'react';
import { EDGES } from '../lib/edgeRouting';

// 엣지 상태 + Kill/Revive 토글 + 현재 사용 중인 엣지 표시
function EdgeMap({ healthMap, currentEdgeId, onToggleEdge, busyEdgeIds }) {
  return (
    <div className="edge-map">
      {Object.values(EDGES).map((edge) => {
        const alive = healthMap[edge.id];
        const isCurrent = currentEdgeId === edge.id;
        const isBusy = busyEdgeIds?.has(edge.id);
        const stateLabel = alive ? 'UP' : 'DOWN';
        return (
          <div
            key={edge.id}
            className={`edge-card ${alive ? 'alive' : 'dead'} ${isCurrent ? 'current' : ''}`}
          >
            <div className="edge-card-header">
              <strong>{edge.label}</strong>
              {isCurrent && <span className="badge badge-active">USING</span>}
            </div>
            <dl className="edge-meta">
              <div><dt>Status</dt><dd>{stateLabel}</dd></div>
              <div><dt>Latency</dt><dd>{edge.delayMs} ms</dd></div>
              <div><dt>Subtitle</dt><dd>{edge.subtitleLang.toUpperCase()}</dd></div>
            </dl>
            <button
              type="button"
              className={`edge-kill ${alive ? 'kill' : 'revive'}`}
              disabled={isBusy}
              onClick={() => onToggleEdge(edge.id, alive)}
            >
              {isBusy ? '...' : alive ? 'Kill Edge' : 'Revive Edge'}
            </button>
          </div>
        );
      })}
    </div>
  );
}

export default EdgeMap;
