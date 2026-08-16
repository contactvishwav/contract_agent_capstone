import React, { useEffect, useState } from 'react';
import { GraduationCap, Sparkles } from 'lucide-react';
import { getChatModelsCached, ModelOption } from '../../../services/modelRegistryApi';

type Tier = 'student' | 'teacher';

/** Same low/medium/high taxonomy backend/model_registry.py already assigns
 * every ModelSpec - "low" cost is the router's student candidate, anything
 * else is treated as a teacher-tier (high-reasoning) model. This is a
 * generalization of Phase 6's two-model router: a manually-selected
 * high-cost model (e.g. GPT-4o) is labeled the same way an auto-routed
 * teacher answer would be, instead of the badge only ever appearing for
 * "auto" requests. */
function tierForCostClass(costClass: string): Tier {
  return costClass === 'low' ? 'student' : 'teacher';
}

interface Props {
  modelId: string;
}

/** Reads the same server-authoritative registry ChatInput's model dropdown
 * already fetches (model_registry.py, via GET /api/models) rather than
 * hardcoding a second, driftable list of which model ids count as
 * "student" vs "teacher" on the frontend. */
export const ModelTierBadge: React.FC<Props> = ({ modelId }) => {
  const [models, setModels] = useState<ModelOption[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getChatModelsCached()
      .then((registry) => {
        if (!cancelled) setModels(registry.models);
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const option = models?.find((model) => model.id === modelId);
  if (!option) return null;

  const tier = tierForCostClass(option.cost_class);
  const label = tier === 'student' ? 'Student Model' : 'Teacher Model';
  const Icon = tier === 'student' ? Sparkles : GraduationCap;
  const classes =
    tier === 'student'
      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
      : 'bg-indigo-50 text-indigo-700 border-indigo-200';

  return (
    <span
      data-testid="model-tier-badge"
      data-tier={tier}
      title={`Answered by ${option.display_label}`}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${classes}`}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
};
