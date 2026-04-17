<script lang="ts">
  let {
    environmentId,
    environmentStatus,
    validationSummary,
    deliverySummary,
    artifactManifest,
  }: {
    environmentId?: string | null
    environmentStatus?: string | null
    validationSummary?: Record<string, unknown> | null
    deliverySummary?: Record<string, unknown> | null
    artifactManifest?: Record<string, unknown> | null
  } = $props()

  const validationStatus = $derived((validationSummary?.status as string | undefined) ?? '')
  const deliveryMode = $derived((deliverySummary?.mode as string | undefined) ?? '')
  const deliveryBranch = $derived((deliverySummary?.branch as string | undefined) ?? '')
  const ciStatus = $derived(
    ((deliverySummary?.ci as Record<string, unknown> | undefined)?.status as string | undefined) ??
      '',
  )
  const artifactEntries = $derived(
    Object.entries(artifactManifest ?? {}).map(([kind, items]) => ({
      kind,
      count: Array.isArray(items) ? items.length : 0,
    })),
  )
</script>

{#if environmentId || validationStatus || deliveryMode || artifactEntries.length > 0}
  <section class="summary" data-testid="harness-summary">
    {#if environmentId || environmentStatus}
      <div class="card" data-testid="harness-summary-environment">
        <div class="label">Environment</div>
        {#if environmentId}<div class="value">{environmentId}</div>{/if}
        {#if environmentStatus}<div class="meta">{environmentStatus}</div>{/if}
      </div>
    {/if}

    {#if validationSummary}
      <div class="card" data-testid="harness-summary-validation">
        <div class="label">Validation</div>
        {#if validationStatus}<div class="value">{validationStatus}</div>{/if}
        {#if validationSummary.last_snapshot}
          <div class="meta">snapshot captured</div>
        {/if}
        {#if validationSummary.last_screenshot}
          <div class="meta">screenshot saved</div>
        {/if}
        {#if validationSummary.last_assertion}
          <div class="meta">
            assertion {((validationSummary.last_assertion as Record<string, unknown>).ok as boolean)
              ? 'passed'
              : 'failed'}
          </div>
        {/if}
      </div>
    {/if}

    {#if deliverySummary}
      <div class="card" data-testid="harness-summary-delivery">
        <div class="label">Delivery</div>
        {#if deliveryMode}<div class="value">{deliveryMode}</div>{/if}
        {#if deliveryBranch}<div class="meta">{deliveryBranch}</div>{/if}
        {#if ciStatus}<div class="meta">ci {ciStatus}</div>{/if}
      </div>
    {/if}

    {#if artifactEntries.length > 0}
      <div class="card" data-testid="harness-summary-artifacts">
        <div class="label">Artifacts</div>
        {#each artifactEntries as entry (`${entry.kind}-${entry.count}`)}
          <div class="meta">{entry.kind} × {entry.count}</div>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
  .summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 8px;
    margin-bottom: 10px;
  }

  .card {
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 10px 12px;
    background: rgba(15, 23, 42, 0.03);
  }

  .label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text2);
    font-weight: 700;
    margin-bottom: 6px;
  }

  .value {
    font-size: 13px;
    font-weight: 700;
    line-height: 1.3;
  }

  .meta {
    font-size: 11px;
    color: var(--text2);
    line-height: 1.5;
  }
</style>
