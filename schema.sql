BEGIN;

CREATE TABLE IF NOT EXISTS empresas (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(140) NOT NULL,
    documento VARCHAR(40),
    status VARCHAR(30) NOT NULL DEFAULT 'Ativa',
    criado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO empresas (id, nome, documento, status)
VALUES (1, 'Loja Horizonte', 'CNPJ: 12.345.678/0001-90', 'Ativa')
ON CONFLICT (id) DO NOTHING;

SELECT setval(
    pg_get_serial_sequence('empresas', 'id'),
    COALESCE((SELECT MAX(id) FROM empresas), 1),
    true
);

-- Adiciona empresa_id em clientes
ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS empresa_id INTEGER;

UPDATE clientes
SET empresa_id = 1
WHERE empresa_id IS NULL;

ALTER TABLE clientes
    ALTER COLUMN empresa_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'clientes_empresa_id_fkey'
    ) THEN
        ALTER TABLE clientes
            ADD CONSTRAINT clientes_empresa_id_fkey
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE RESTRICT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_clientes_empresa_id ON clientes(empresa_id);

-- Adiciona empresa_id em produtos
ALTER TABLE produtos
    ADD COLUMN IF NOT EXISTS empresa_id INTEGER;

UPDATE produtos
SET empresa_id = 1
WHERE empresa_id IS NULL;

ALTER TABLE produtos
    ALTER COLUMN empresa_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'produtos_empresa_id_fkey'
    ) THEN
        ALTER TABLE produtos
            ADD CONSTRAINT produtos_empresa_id_fkey
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE RESTRICT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_produtos_empresa_id ON produtos(empresa_id);

-- Adiciona empresa_id e campos de pagamento à vista em vendas
ALTER TABLE vendas
    ADD COLUMN IF NOT EXISTS empresa_id INTEGER,
    ADD COLUMN IF NOT EXISTS forma_pagamento VARCHAR(30) NOT NULL DEFAULT 'À vista',
    ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'Concluída';

UPDATE vendas
SET empresa_id = 1
WHERE empresa_id IS NULL;

UPDATE vendas
SET forma_pagamento = 'À vista'
WHERE forma_pagamento IS NULL OR btrim(forma_pagamento) = '';

UPDATE vendas
SET status = 'Concluída'
WHERE status IS NULL OR btrim(status) = '';

ALTER TABLE vendas
    ALTER COLUMN empresa_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vendas_empresa_id_fkey'
    ) THEN
        ALTER TABLE vendas
            ADD CONSTRAINT vendas_empresa_id_fkey
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vendas_forma_pagamento_avista_check'
    ) THEN
        ALTER TABLE vendas
            ADD CONSTRAINT vendas_forma_pagamento_avista_check
            CHECK (forma_pagamento IN ('À vista', 'Dinheiro', 'Pix', 'Cartão à vista'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_vendas_empresa_id ON vendas(empresa_id);
CREATE INDEX IF NOT EXISTS idx_vendas_empresa_criado_em ON vendas(empresa_id, criado_em DESC);

-- Adiciona empresa_id e criado_em em itens_venda
ALTER TABLE itens_venda
    ADD COLUMN IF NOT EXISTS empresa_id INTEGER,
    ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;

UPDATE itens_venda iv
SET empresa_id = v.empresa_id
FROM vendas v
WHERE iv.venda_id = v.id
  AND iv.empresa_id IS NULL;

UPDATE itens_venda
SET empresa_id = 1
WHERE empresa_id IS NULL;

ALTER TABLE itens_venda
    ALTER COLUMN empresa_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'itens_venda_empresa_id_fkey'
    ) THEN
        ALTER TABLE itens_venda
            ADD CONSTRAINT itens_venda_empresa_id_fkey
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE RESTRICT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_itens_venda_empresa_id ON itens_venda(empresa_id);

COMMIT;
