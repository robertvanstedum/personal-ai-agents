/* Reusable safe DOM presentation. Never parses a message body as JSON/HTML. */
window.RecordsContribution = {
  render(view, availableRecordIds) {
    if (!view || view.type !== "agent_contribution") return null;
    const make = (tag, text, cls) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (cls) node.className = cls;
      return node;
    };
    const node = make("section", undefined, "agent-contribution");
    node.append(make("div", view.text, "event-body"), make("p", view.warning, "agent-warning"));
    const details = make("details", undefined, "agent-evidence");
    details.append(make("summary", "Evidence"));
    const evidence = view.evidence;
    details.append(make("p", evidence.assurance));
    const list = make("dl");
    for (const [key, label] of Object.entries({coordination_request_id:"Request",runtime_id:"Runtime ID",record_id:"Saved record",receipt_id:"Write receipt"})) {
      list.append(make("dt", label), make("dd", evidence[key]));
    }
    details.append(list, make("p", "Context source records (not all necessarily cited in the reply):"));
    for (const id of evidence.source_record_ids) {
      if (availableRecordIds.has(id)) {
        const link = make("a", id); link.href = `#event-${id}`;
        link.onclick = event => {event.preventDefault(); document.getElementById(`event-${id}`)?.scrollIntoView({block:"center"});};
        details.append(link, make("br"));
      } else details.append(make("div", `${id} · not in this view`));
    }
    node.append(details);
    return node;
  }
};
