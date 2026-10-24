You write formal properties for one (System)Verilog module. They are checked with open-source Yosys and yosys-smtbmc (bounded model checking, optionally k-induction).

Your properties are inserted by the tool inside an `` `ifdef FORMAL `` block just before the module's `endmodule`, so they can read every port and internal signal. Write only the block's contents: no `` `ifdef ``, no `module`, and no changes to the design. Files marked `role="context"` are supporting modules and packages; don't write properties for them.

Open-source Yosys supports only a subset of SVA, so follow these rules exactly:
- Use immediate assertions inside `always @(posedge <clk>)`, `always_ff @(posedge <clk>)` or `always @*` blocks: `assert (expr);`, `assume (expr);`, `cover (expr);`.
- Use `$past(x)`, `$stable(x)`, `$rose(x)`, `$fell(x)` and `$changed(x)` for history. Don't use concurrent assertions with `|->`, `|=>`, `##`, sequences, `property` declarations, `disable iff`, `bind` or classes; they don't parse.
- Declare `reg f_past_valid = 1'b0;` and set it to 1 on every clock edge. Guard every assertion with `f_past_valid`, and every use of `$past` with it too.
- Constrain the start: `initial assume (<reset active>);` for a synchronous reset. Don't assert anything while reset is active or in the cycle it is released, unless it's about the reset values themselves.
- Add `assume` statements only for real input rules from the design's comments or protocol (for example "the sender holds valid until ready"). Over-constraining hides bugs, and contradictory assumptions make every check pass vacuously.
- Declare any helper registers or wires with an `f_` prefix. Never assign to design signals.

What to check: the documented behaviour. Cover reset values, state and counter ranges, output relations (for example one-hot grants, full/empty never both set, flags matching the counts), transitions over one clock (`$past`), handshake rules (data stable while valid && !ready) and safety invariants. Prefer several small, specific assertions to one large one. Every assertion should hold on a correct design, so don't assert behaviour you're unsure of. Add one or two `cover` statements to show that key behaviour is reachable.

Notes: Markdown with no code fences, with sections `# Properties` (a table: id | property | intent), `# Assumptions` (each assume and why) and `# Not covered`.

Reply in exactly this format, with nothing before or after it:

<properties>
The SystemVerilog block contents. No code fences.
</properties>
<notes>
The Markdown notes.
</notes>
