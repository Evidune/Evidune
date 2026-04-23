<script lang="ts">
  import type { Skill } from '../lib/types'
  import { activeSkills } from '../lib/stores'

  let { skills }: { skills: Skill[] } = $props()
  let active: string[] = $state([])

  activeSkills.subscribe(v => active = v)
</script>

{#if skills.length > 0}
  <div class="skills-bar">
    {#each skills as skill}
      <span
        class="skill-tag"
        class:active={active.includes(skill.name)}
        class:inactive={skill.status && skill.status !== 'active'}
        title={`${skill.description || skill.name}${skill.status ? ` · ${skill.status}` : ''}`}
      >
        {skill.name}
        {#if skill.source === 'emerged'}
          <small>learned</small>
        {/if}
      </span>
    {/each}
  </div>
{/if}

<style>
  .skills-bar {
    display: flex;
    gap: 6px;
    padding: 10px 20px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    overflow-x: auto;
    flex-shrink: 0;
  }

  .skill-tag {
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 100px;
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text2);
    white-space: nowrap;
    cursor: default;
    transition: all 0.2s;
  }

  .skill-tag.active {
    border-color: var(--accent);
    color: var(--accent2);
    background: rgba(99, 102, 241, 0.1);
  }

  .skill-tag.inactive {
    opacity: 0.55;
  }

  .skill-tag small {
    margin-left: 6px;
    color: var(--text2);
  }
</style>
