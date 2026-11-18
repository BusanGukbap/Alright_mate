const form = document.getElementById('expression-form');
const fields = document.getElementById('expression-fields');
const phrase = document.getElementById('phrase');
const meaning = document.getElementById('meaning');
const list = document.getElementById('expression-list');
const listStatus = document.getElementById('list-status');
const saveStatus = document.getElementById('save-status');
const endpoint = '/api/expressions';
let listLoaded = false;

function expressionRow(expression) {
  const row = document.createElement('div');
  const term = document.createElement('dt');
  const definition = document.createElement('dd');
  term.textContent = expression.phrase;
  definition.textContent = expression.meaning;
  row.append(term, definition);
  return row;
}

async function loadExpressions() {
  listStatus.textContent = 'Loading expressions…';

  try {
    const response = await fetch(endpoint);
    if (!response.ok) throw new Error('Could not load expressions.');

    const expressions = await response.json();
    for (const expression of expressions) list.append(expressionRow(expression));

    listLoaded = true;
    listStatus.textContent = expressions.length ? '' : 'No expressions saved yet.';
  } catch {
    listStatus.textContent = 'Could not load saved expressions. Refresh the page to try again.';
  } finally {
    fields.disabled = false;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (fields.disabled) return;

  const expression = { phrase: phrase.value.trim(), meaning: meaning.value.trim() };
  if (!expression.phrase || !expression.meaning) {
    saveStatus.textContent = 'Enter an expression and its meaning.';
    (expression.phrase ? meaning : phrase).focus();
    return;
  }

  fields.disabled = true;
  saveStatus.textContent = 'Saving expression…';
  const saveError = 'Could not save the expression. Try again.';

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(expression),
    });
    const data = await response.json();
    if (!response.ok) {
      saveStatus.textContent = typeof data?.error === 'string' ? data.error : saveError;
      return;
    }

    list.prepend(expressionRow(data));
    if (listLoaded) listStatus.textContent = '';
    form.reset();
    saveStatus.textContent = 'Expression saved.';
  } catch {
    saveStatus.textContent = saveError;
    return;
  } finally {
    fields.disabled = false;
  }

  phrase.focus();
});

loadExpressions();
