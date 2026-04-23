<script lang="ts">
  import type { SkillCreation } from '../lib/types'

  let { creation }: { creation: SkillCreation } = $props()
</script>

<div class="skill-creation" data-status={creation.status}>
  <strong>Skill {creation.status}</strong>
  <span>{creation.skill_name || 'pending'}</span>
  {#if creation.reason}
    <small>{creation.reason}</small>
  {/if}
</div>

<style>
  .skill-creation {
    display: grid;
    gap: 2px;
    margin: 6px 0;
    padding: 9px 11px;
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 10px;
    background: var(--surface);
    font-size: 12px;
    color: var(--text2);
  }

  .skill-creation strong {
    color: var(--text);
    text-transform: capitalize;
  }

  .skill-creation span {
    color: var(--accent2);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  .skill-creation small {
    color: var(--text2);
  }

  .skill-creation[data-status='failed'] {
    border-left-color: #ef4444;
  }

  .skill-creation[data-status='reused'],
  .skill-creation[data-status='queued'] {
    border-left-color: #f59e0b;
  }
</style>
