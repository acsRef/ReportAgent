import { useState, useCallback } from 'react';
import { useDashboardStore } from '../stores/dashboardStore';
import CanvasToolbar from '../components/dashboard/CanvasToolbar';
import DesignCanvas from '../components/dashboard/DesignCanvas';
import ComponentLib from '../components/dashboard/ComponentLib';
import PropertyPanel from '../components/dashboard/PropertyPanel';
import { generateDashboard } from '../services/dashboardApi';
import './DashboardPage.css';

export default function DashboardPage() {
  const isEditing = useDashboardStore((s) => s.isEditing);
  const setGenerating = useDashboardStore((s) => s.setGenerating);
  const setDashboardConfig = useDashboardStore((s) => s.setDashboardConfig);
  const resetDashboard = useDashboardStore((s) => s.resetDashboard);

  const [aiInput, setAiInput] = useState('');

  const handleGenerate = useCallback(async () => {
    const text = aiInput.trim();
    if (!text) return;
    setGenerating(true);
    try {
      const result = await generateDashboard({ user_requirement: text });
      setDashboardConfig(result.dashboard);
    } catch (err) {
      console.error('生成看板失败:', err);
    } finally {
      setGenerating(false);
    }
  }, [aiInput, setGenerating, setDashboardConfig]);

  return (
    <div className="dashboard-page">
      {/* AI Input Bar */}
      <div className="dashboard-ai-bar">
        <div className="dashboard-ai-bar-inner">
          <span className="dashboard-ai-bar-icon">🤖</span>
          <input
            className="dashboard-ai-input"
            value={aiInput}
            onChange={(e) => setAiInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleGenerate()}
            placeholder="描述你想生成的看板，如：按区域分析销售趋势，展示核心 KPI..."
          />
          <button className="btn btn-primary" onClick={handleGenerate} style={{ flexShrink: 0 }}>
            ✨ 生成
          </button>
          <button className="btn btn-outline" style={{ flexShrink: 0 }} onClick={resetDashboard}>
            🗑️ 清空
          </button>
        </div>
      </div>

      {/* Canvas Toolbar (zoom, resolution) */}
      <CanvasToolbar />

      {/* Main Content: Lib + Canvas + Properties */}
      <div className="dashboard-body">
        {isEditing && <ComponentLib />}
        <DesignCanvas />
        {isEditing && <PropertyPanel />}
      </div>
    </div>
  );
}