# Node SDK Generation

Regenerado a partir da estrutura medida no `starkbank/sdk-node`, não de suposição.
Ver `tests/contract/node-transfer.contract` para o contrato extraído.

## Artefatos por recurso

Três arquivos, produzidos por **três runs** do gerador:

| Papel | Generator | Saída do gerador | Destino no SDK |
|---|---|---|---|
| `impl` | `javascript` | `src/model/{X}.js` | `sdk/{x}/{x}.js` |
| `barrel` | `javascript` | `src/model/{X}.js` | `sdk/{x}/index.js` |
| `types` | `typescript-node` | `model/{x}.ts` | `types/{x}/{x}.d.ts` |

O `typescript-axios` **não** produz arquivo com template de modelo customizado — foi medido.
Não usar.

Recursos com sub-recurso (`log`, `rule`) chegam a 7–8 arquivos. Fora do escopo atual.

## Shape

Funções de módulo, não métodos estáticos. Consumidores chamam
`starkbank.transaction.get(...)`, nunca `Transaction.get(...)`.

```javascript
const rest = require('../utils/rest.js');
const check = require('starkcore').check;
const Resource = require('starkcore').Resource;

class Transaction extends Resource {
    constructor({ id, amount, created }) {
        super(id);
        this.amount = amount;
        this.created = check.datetime(created);
    }
}

exports.Transaction = Transaction;
let resource = {'class': exports.Transaction, 'name': 'Transaction'};

exports.get = async function (id, {user} = {}) {
    return rest.getId(resource, id, user);
};
```

Pontos que divergem do padrão default do gerador:

- descritor `let resource = {'class':…, 'name':…}`, não `ClassData`
- `user` posicional em `rest.*`, não desestruturado
- `check.datetime()` nos campos com `format: date-time`
- construtor recebe objeto desestruturado
- `require('starkcore')`, não caminho relativo
- JSDoc dentro do corpo da função

## Operações

Dirigidas pela spec via `x-sdk-create`, `x-sdk-put`, `x-sdk-get`, `x-sdk-query`, `x-sdk-page`.
O barrel enumera só as declaradas. Sem flag, nenhum método é gerado.

## Como gerar

```bash
python3 tools/build-resource.py Transaction --lang node --into <repo-do-sdk>
```

Encadeia `lint-spec` → 3 runs → `assert-generated` por artefato → `place-generated`.

## Desvio conhecido

`query`/`page` recebem `{user, ...query}` genérico. O SDK real lista os filtros
explicitamente (`limit`, `after`, `before`, `tags`, `ids`…); a spec declara dois. A lista
explícita fica possível quando a spec declarar os parâmetros.
