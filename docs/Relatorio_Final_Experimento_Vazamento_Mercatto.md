# **Mercatto Varejo — Relatório de resultados do experimento de vazamento**

#  **Equipe:** Gabriel Siqueira Matias (2023278440020); Krystyan Douglas Santos Costa (2023278440026); Pedro Alexandre Souza de Campos (2023278440034)

**Disciplina:** Mineração de Dados — IFMT

**Consolidação:** XX/10/2026

**1\. Pergunta analítica e procedimento**  
A pergunta é se um cliente com compras registradas até 30/06/2025 voltará a comprar entre 01/07/2025 e 28/09/2025. Construíram-se duas tabelas-base de análise (ABTs) com 108.064 clientes e 15 atributos preditivos. A versão correta limita os atributos ao corte; a versão de comparação introduz, propositalmente, compras futuras em recência e frequência e usa z-score global de valor\_monetario antes do particionamento.

| População e classe | Protocolo de avaliação |
| :---- | :---- |
| 108.064 clientes; 24.043 positivos (22,25%) | Alvo binário: recompra não cancelada nos 90 dias após o corte |
| 75.645 treino / 32.419 teste | Divisão 70/30 estratificada e determinística no Orange |
| Teste: 25.206 classe 0; 7.213 classe 1 | Tree, Logistic Regression e Naive Bayes; Test & Score |

As versões principais usaram a mesma configuração e a mesma divisão dos clientes; foram examinados AUC, matriz de confusão, ROC e acurácia balanceada (BA). O relatório responde, na ordem, às três perguntas exigidas no enunciado.

# **2\. Questão a**

# **2.1 Qual a diferença de desempenho?**

| Modelo | AUC correta | AUC vazamento | Δ AUC | BA correta | BA vazamento | Δ BA (p.p.) |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| Tree | 0,496 | 0,965 | \+0,469 | 51,19% | 94,73% | \+43,54 |
| Regressão logística | 0,578 | 0,873 | \+0,295 | 50,06% | 65,23% | \+15,17 |
| Naive Bayes | 0,570 | 0,878 | \+0,308 | 52,35% | 78,01% | \+25,66 |

**AUC do mesmo conjunto de teste**  
![][image1]

# 

# **2.2 Leitura crítica dos resultados**

A Tree exibiu o maior salto: de AUC 0,496 para 0,965, enquanto a BA passou de 51,19% para 94,73%. A diferença de 43,54 pontos percentuais não representa melhora utilizável em produção: a ABT com vazamento incorpora justamente eventos posteriores à data em que a previsão deveria ser emitida.

## **Matrizes de confusão: classe positiva \= recomprou**

| Modelo e versão | TN | FP | FN | TP | Recall classe 1 |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Tree — correta | 21.607 | 3.599 | 6.012 | 1.201 | 16,65% |
| Tree — vazamento | 24.680 | 526 | 610 | 6.603 | 91,54% |
| Regressão — correta | 25.179 | 27 | 7.196 | 17 | 0,24% |
| Regressão — vazamento | 23.157 | 2.049 | 4.429 | 2.784 | 38,60% |
| Naive Bayes — correta | 22.898 | 2.308 | 6.213 | 1.000 | 13,86% |
| Naive Bayes — vazamento | 20.970 | 4.236 | 1.960 | 5.253 | 72,83% |

TN \= verdadeiro negativo; FP \= falso positivo; FN \= falso negativo; TP \= verdadeiro positivo. A BA é calculada como \[TP/(TP+FN) \+ TN/(TN+FP)\]/2. Assim, cada classe tem peso igual, ao contrário da acurácia convencional.

## **Interpretação para o negócio**

**•  Acurácia comum pode iludir.** Apenas prever “não recompra” já atingiria cerca de 77,75% de acertos neste teste, dada a prevalência de 25.206 negativos entre 32.419 clientes. A regressão correta obteve 77,72% de acurácia, porém identificou só 17 das 7.213 recompras efetivas.  
**•  O contraste na Tree é especialmente revelador.** Na versão correta, somente 1.201 recompras foram identificadas; com vazamento foram 6.603. Essa melhoria aparente acompanha a inclusão de informação futura, não uma demonstração de capacidade preditiva prospectiva.  
**• AUC e BA contam histórias complementares.** AUC mede a ordenação das probabilidades em diferentes limiares. BA resume decisões discretas no limiar aplicado pelo classificador. Por isso, os indicadores devem ser examinados juntos.

## **O que se pode e não se pode concluir** 

Os resultados demonstram o impacto conjunto dos dois atributos temporais contaminados e da normalização global. Não é possível atribuir todo o aumento à normalização, nem separar matematicamente a contribuição individual de recencia\_dias e frequencia\_pedidos sem testes adicionais de ablação. O experimento controlado seguinte isola apenas o terceiro vazamento.

# 

**3\. Questão b**

# **3.1 A normalização global produziu diferença perceptível?**

Foi realizado um experimento complementar com a ABT historicamente correta. Mantiveram-se exatamente os mesmos 75.645 clientes de treinamento, os mesmos 32.419 de teste, o alvo e as outras 14 features. Alterou-se somente o z-score de valor\_monetario: em uma versão média e desvio foram estimados no treino; na outra, na base inteira antes da separação.

| Parâmetro de valor\_monetario | Treino (seguro) | Base inteira (global) |
| :---- | :---- | :---- |
| Média (R\$) | 69.003,70 | 68.966,34 |
| Desvio padrão (R\$) | 5.108,87 | 5.096,32 |

## **Resultados do teste controlado**

| Modelo | AUC segura | AUC global | BA segura | BA global | Δ BA (p.p.) |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Regressão logística | 0,577 | 0,577 | 50,1108% | 50,1128% | \+0,0020 |
| Tree | 0,509 | 0,509 | 51,3778% | 51,3778% | 0,0000 |
| Naive Bayes | 0,565 | 0,565 | 52,8407% | 52,8407% | 0,0000 |

A AUC foi igual nas três casas decimais exibidas pelo Orange em todos os modelos. Tree e Naive Bayes mantiveram matrizes idênticas. Na regressão logística, o número de falsos positivos caiu de 21 para 20, sem alteração dos 22 verdadeiros positivos; isso explica o aumento de aproximadamente 0,0020 ponto percentual na BA.

## **3.2 Por que o ganho foi quase nulo?**

As estatísticas obtidas do treino e da base inteira são muito próximas. Além disso, o z-score é uma transformação linear crescente que preserva a ordenação dos valores; árvores costumam manter decisões equivalentes diante desse tipo de transformação. Para outros algoritmos, ajustes internos e regularização podem produzir pequenas diferenças.

**3.3 Conclusão da questão**  
O vazamento de estatísticas do teste ocorreu metodologicamente, mas não gerou diferença perceptível de AUC neste conjunto. Vazamento é uma falha do procedimento, mesmo quando não aumenta a pontuação. O ganho dramático da ABT contaminada principal, portanto, não pode ser creditado ao z-score antecipado.

Observação de comparabilidade: o teste isolado utilizou sua própria divisão fixa de clientes, idêntica nas variantes segura e global, mas não necessariamente igual à divisão do primeiro par de ABTs. As comparações válidas são dentro de cada par, não entre seus valores absolutos.

# **4\. Questão c**

# **4.1 Como descobrir o vazamento sem conhecer o código?**

Se recebêssemos apenas a avaliação aparentemente excelente da Tree (AUC 0,965; BA 94,73%), ela seria um sinal de investigação, não prova automática de erro. O diagnóstico exigiria refazer o processo de validação e auditar a disponibilidade temporal dos dados.

**•  1\. Verificar a separação de treino e teste.** Confirmar que nenhum cliente ou linha aparece nos dois conjuntos e que transformações aprendidas dos dados (imputação, escala e seleção) são ajustadas apenas no treino. Avaliar novamente em amostra não utilizada na modelagem.  
**•  2\. Simular a data real da previsão.** Para cada atributo, documentar a fonte e a data em que o valor ficaria disponível. Recalcular as features em 30/06/2025, vedando qualquer pedido ou atendimento posterior; usar apenas eventos posteriores para definir o alvo.  
**•  3\. Procurar sinais concretos de impossibilidade.** Investigar recencia\_dias negativa no corte, frequência que inclui pedidos futuros, datas e atributos modificados após o corte e variáveis com correlação suspeita com o alvo. A recência negativa observada na base contaminada é uma pista operacional verificável.  
**•  4\. Executar testes de retirada de atributos.** Remover recência e frequência contaminadas separadamente, refazer a avaliação e observar a queda de AUC/BA. Comparar o resultado com uma ABT reconstruída sem informações futuras para verificar a hipótese causal.  
**•  5\. Usar validação temporal externa.** Treinar em período anterior e avaliar em uma janela cronologicamente posterior, sem recalcular atributos com informações vindouras. Comparar resultados de diferentes cortes e meses, em vez de confiar exclusivamente em uma partição aleatória.

## **4.2 Limites e riscos residuais**

A versão correta passou 5/5 asserções (incluindo conferência independente de recência e frequência em amostra de 100 clientes) e a versão contaminada passou 4/4 controles estruturais. Essas aprovações não provam, sozinhas, ausência completa de vazamento: o DW não mantém todas as versões históricas dos status e das categorias. O trabalho deve declarar essa limitação e não interpretar a ABT correta como uma fotografia temporal perfeita de todas as fontes.

## **4.3 Conclusão**

No caso Mercatto, o principal aprendizado não é que um modelo atingiu 96,5% de acurácia, mas que essa pontuação pode resultar de dados indisponíveis no instante da decisão. O resultado confiável para comparação operacional é o da ABT histórica, apesar de seu desempenho modesto; a versão contaminada é um contraexemplo intencional. O experimento adicional mostrou que o z-score global é incorreto, mas isoladamente não melhorou a AUC de maneira perceptível.

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnUAAACvCAYAAACbzSg0AAAflklEQVR4Xu3dCZgU1b3+ccSALAIJUUBRQFERNy5gfFCWCbKIV1AC5A+KBhdWAcEgikIcZVVEDIgkcUMFNzBANF6jIGBkUa8gil5EkImCiIFBZB0W+d3/78ytY/WZXkanmZo+8/08z/tM1enqpuF0Vb9Ud0+XEQAAAGS8Mu4AAAAAMg+lDgAAwAOUOgAAAA9Q6gAAADxAqQMAAPAApQ4AAMADlDoAAAAPUOoAAAA8QKkDAADwAKUOAADAA5Q6AAAAD1DqAAAAPECpAyJUM3ty2lJY9erVkzJlYnf9GTNmSKNGjeQPf/iD1KpVS1q2bGnGs7OzTbKyskyC9eKmf+bixYvd4aNm5eA2aU1hHDlyRI455hgZNmyYHQvmRf/+ffr0MfN2+PBhmTdvXszcRDUvmaZu3bruEOAVSh0QIbeYFSWFpcVAi8MLL7xgx7Q89OrVK2absFSlYe3ateYJ88QTT5Tp06fb8csuu0zKly8vO3futGN62/fee68Zj7c+cOBAs9ypU6eY6wRR69atk6ZNm0qdOnVk06ZNdrt0cUtZUVMYXbt2lWXLlsX827vz0qVLF5k9e7ZdV8nmRd1www3m37NFixZ2LJiXO+64w45pOTx48KCcfvrp8uijj5qx8847T2rXri3ff/+93S7QoEED2bZtm10P7veiRYukYcOGUqFCBbnnnntiLnfnUcvpmWeeKZUqVZIpU6bYbfXv/PDDD8txxx0n48aNM2O/+tWvzONr3759djt93J1yyily9dVX2zG9bk5OjrnvNWrUsI89989W8f4dgExGqQMi5BazoqQwJkyYIOPHjzfLelYoED5Td/LJJ8u0adPsZSpZqdMn9nLlytn1oGTpk6U+6arwE6kuu2UgWD906JAsWbLELH/11VfmrKHSPzt8pm7z5s12uWzZsnY5XdxSVtQURvBvpGU1ED5T17dvX6lcubK9LJBoXlTVqlXtv2cgPC8DBgwwZwCVlroLL7zQLOt86hld9e2338oJJ5yQf+UQnefgvr733numcKrPP//cbjNq1KiY/zwoLV1Dhw41yxs2bLDjv/vd7+Sdd96x2wRz/Mtf/tL++Xl5ebb879ixw1ym1qxZI40bN7bXPfXUU82yPp7Cj73wmbpE/w5AJqPUARFyi1lRUhjhJzh9UtMzMyp8RmjWrFlSpUoVu51KVuq6desmr732mjsc82e1atVKVq9eXWDcXdczJsGfpQku0+VwqdOXKtu0aVPgzEu6uKWsqEll4cKF5qyRys3NjTsvW7duNSVm5cqVwdWMRPOi4v3buGPBupa64KxWz5497XyFt3EF49WrVzcFSu3Zs8eUQ/1Pg17evn17u70+toLiqLZv3y7nn3++nUctrir4O6uRI0fK/Pnz7XrwZ3bv3t0UMfexotf94IMPCmyvwqXO/Tu560Am4lEMRMgtZkVJKv/+97/tk2eQdu3amcvcl/ncJ7jgiTOewpa64InWve3wevj9ZGFuqdOzc7t27TLLZ511lh1PF7eUFTWpaMEuzLx89NFH8vOf/9yuq0Tzotx/63hjwbqWuoD+mfoSZsC9TqB3797mZ/jy8PKWLVtibrdatWp2WYW3Xb58uf27hv/O7twH19GXqz/88EM7Hkh23yl18B2PYiBCbjErSlLR8vPuu+/GjAVPZOHyoG/Ed5/gkpW6RC+/6tj69evNcqInfXddz1C9+uqrdl2f6NUjjzwiTz75pB3X6+j93L17d4HbSwe3lBU1qbh/h3jzovQlziuvvNKuq0TzovTl17feeitmLDwvgwYNssXsp5Q6NXr0aJk8+YfHX3hbfc9jcLvNmjWz44Hwtnpff0yp07N8+h67QLBNsvuu79ELJPp3ADJZ4j0VwFHnFrOiJJV4T8x6Rmju3LmmPOjlmrPPPtu8ny0sWalTH3/8sXkvnr4x/S9/+Ysd19vXs1D6vqyAez/cdX0JtmLFinLGGWfIzJkz7bi+tyzYdu/evVK/fn25+eabY8pIurilrKhJ5pprron5kICKNy/6wYMhQ4bEbKeSzYu67rrrTIHRs6WBYF6GDx9ux35qqXMv+/LLL81j4ZxzzjEvyQa3G/w9gqhPPvnEFLMmTZqYP+/HlDql19HHiX5oQz90oZLd9/79+8esx/t3ADJZ4j0VAAAAGYNSBwAA4AFKHQAAgAcodQAAAB6g1AEAAHiAUgcAAOABSh0AAIAHKHUAAAAeoNQBAAB4gFIHAADgAUodAACAByh1AAAAHqDUAQAAeIBSBwAA4AFKHQAAgAcodQAAAB6g1AEAAHiAUgcAAOABSh0AAIAHKHUe+f77790hZDCdT+bUL8ypn5hT/3z99dfuUEag1HmEA4tfKAD+YU79xJz6h1KHyHFg8QsFwD/MqZ+YU/9Q6hA5Dix+oQD4hzn1E3PqH0odIseBxS8UAP8wp35iTv1DqUPkamZPJoSU0ChKnZ+YU/9Q6hA590mEEFJyoih1fmJO/UOpQ+TcJxFCSMmJSlTqcjZ/IzWb3yhjps8xPxM5dOiwnN52gNx876N2TLe/Y9JMadLlNrnvsXl2/LN/bZGTWt4kox+ZLTfcOU327N1vL0N6xZtTZDZKHSLnPokQQkpOVKJSp8UsGN+9d5/s2ZfnbJGvdqs+snTl2phS98ay1XY5XAiTlUOkV7w5RWaj1CFy7pMIIaTkRCUrdWEz5i6KWVcjH3pWPt34VYFSt2D5h3ZZb2df3gE5eOiQnNtxqCmBOnZa2wF2G6RfvDlFZqPUIXLukwghpOREFbbUTXnm7zHrO3ftlf/sO9Ysu6VOrzviwVnStOtwOfOygfLv3J2yddu3Mbf52JyF0v3WB+060ivenCKzUeoQOfdJhBBScqIKW+r++d+fxKxf2ivbbBOOjrkSvfyqL+fWad3XriO94s0pMhulDpFzn0QIISUnKlGpCz4oMfZPLyUsZgH3TF2PWyebD1jUanGTrP18sx2f9OTfzEuwd06eZW5nf95BexnSK96cIrNR6mD16tXLpFGjRlK/fn27vm3bNnfTtHKfRAghJScqUamL55P1X8qTf33THUYJVNg5Reag1KGA7OxsU+aKi/skQggpOVE/ptQhczCn/qHUoYBwqcvJyZGLLrpIqlatKt26dTNjjRs3lhNPPFE6duwoXbp0sdcLj5cpU0aOHDliL0vGfRIhhJScKEqdn5hT/1DqUIBb6rSgBVavXi0XXHCBXS9fvnzc8S1btpiXcV16W27cJxFCSMmJotT5iTn1D6UOBbilTs/UBQYMGFCglG3evDnueLgMJrNycBtCCCGkVCcdKHUowC11WVlZ9rKxY8eauBKNF4b7wCaEEEJKW9KBUocCkpU6pWfgcnNzzfK6devijqs5c+bY5WTcBzYhhBBS2pKK/qqf4HuWc3fudi+Wp+YtltpZfST74Rdifq2Qfveyrut3LQ8Z94Q88VL+p9P11wx1Hni/THx8vkmUKHVHUapSd+DAAWnVqpWUK1dORo8eHXf8kksuiSl8ybgPbEIIIaS0JRn9nZAXdrvdLOv3LNdr09/ZIv/3QwZn6g4f/l727s//LuZTfx3/F3hrqYu6zAUodR5xH9iEEEJIaUsy90x7UaY/9w+7Hu8XfIdLnX4IRs/cBeNNutxmv9UlKHta6oKxG0c+EtxMJCh1HnEf2IQQQkhpSzLD7ntKZr38ll2PV+omz3hZTsnqYwqgflPL1JmvmnHddteefWZZv1/5pJY3ha9m/Krb7fLaP1e5w8WGUucR94FNCCGElLYkU5iXX1Vwpk63D7gF0F1X/e7+szw867/c4WJDqfOI+8AmhBBCSltS0Q9KBN+zHHxQ4qqbJ8g7qz8zy/oBif53T5fBYx83Z+oC+tV9uq5n8PS673203ow37Tpc+t79J+nQe4zUbtXHbh8FSp1H3Ac2IYQQUtryU7hn3fiVJoic+8AmhBBCSlvSgVKHyLkPbEIIIaS0JR0odYgc3z/oF74n1D/MqZ+YU/9Q6hA5Dix+oQD4hzn1E3PqH0odIseBxS8UAP8wp35iTv1DqUPkOLD4hQLgH+bUT8ypfyh1iBwHFr9QAPzDnPqJOfUPpQ6R48DiFwqAf5hTPzGn/qHUIXIcWPxCAfAPc+on5tQ/lDpEjgOLXygA/mFO/cSc+odSh8hxYPELBcA/zKmfmFP/UOoQOQ4sfqEA+Ic59RNz6h9KHSLHgcUvFAD/MKd+Yk79Q6lD5Diw+IUC4B/m1E/MqX8odYgcBxa/UAD8w5z6iTn1D6UOkePA4hcKgH+YUz8xp/6h1CFyHFj8QgHwD3PqJ+bUP5Q6RK5m9mRCCPlRQdFR6vxDqUPk3IM1IYSkCoqOUucfSh0i5x6sCSEkVZLJ2fyN1Gx+o4yZPsf8zN25293EjE98fL5NIDzW9ZaJ0nP4H814p/7jpcU1IyX74RfMdT/d+JW9Tqai1PmHUofIuQdrQghJlWS0dAWFZffefVKvTX9ni/xtUtFtDh/Ov53w9h9++i+5dvgUu56pKHX+odQhcu7BmhBCUiUZt7C568FYkCdeetO9WPblHZCTW/W26zfeNU1a9hwl90x7UU7J6iOHDh0ObZ2ZKHX+odQhcu7BmhBCUiUZt8S5667wGblAx/7j5fW3P7DrvUdNl6fnLZZde/ZJs+4j5LV/rgptnZkodf6h1CFy7sGaEEJSJRm3xLnrrjbXZ8v7azbEjLnXSbWeiSh1/qHUIXLuwZoQQlIlme63PiiPzVlolu+e+rwMn/i0We488H5ZunJteFPZs3d/gYI2+7VlBd4zp9vkHTholsf+6SW5rPeYmMszEaXOP5S6UuzSSy+VBQsWuMPFzj1YE0JIqqRyy7gnzHvfRv3xOTt29uWD7Xvh2t5wj9Ru1ceUM/0wRVi8l2O37dhlPv2qt6m37QNKnX+8L3VlypSRXr16SdWqVeUXv/iFe3Hk9H7VqFHD3M8f66dcJ/D+++/L4MGD3WErKytLcnJy7PqMGTMkOzvbrqeTe7AmhJBU+SnO6zTUHSrVKHX+KRWlLry8f//+0KWZrSilLhW31B1N7sGaEEJSBUVHqfNPqSt1gWOPPVaWLVtmll955RWpU6eO3WbDhvw3zLZt27bA9cNF58orrzQ/8/Ly7HaLFy+Wpk2b2m2WLFlirhPcvnrzzfyPz3/xxRd2bOPGjeY+qVtuuUWaNWtmljdt2pSwvAXjus0xxxwTM75nzx6zrOPvvfeeHDx40IzrGTcVLm3h2//2228LXK7CZ+omTJggZ555plnet++Hly127Nhhfh4+fDjpfXbjHqwJISRVUHSUOv+UilIXJPjL6gO5fPnyBbYL/3TH3eXZs2fbZXXFFVfIp59+KuvXr5cqVarItm3b7GW7du2Sn/3sZ7J58+bQNfItXLhQ+vfvb14iTnQfqlWrFvesWbBd69atZc6cOXZ88uTJ5vbC26guXbokLHUvv/yy3c69XIVLnXv/Alu2bJGhQ4eaP1tfUs7NzXU3iWvl4DaEEEJI2lPalIpSp/Q9ZMGy/qUrVqxoykc4SgtZWLjA1K1b1y7r2Sr3+mvX5n+qSm//t7/9rbnuNddcY8b0LNZ1111nxrQwqQ4dOkjnzp3tGa5Epa5Ro0aydOnSmDEVbNegQQPz9wvMmzfPnGUMb6O0cMUrdUr/Pscff7yULVs27uWpSt2KFSvM+wOD61x88cVxi2g87k5ICCGEpCOlTakpdeqcc86RMWPyP4Yer5io8Pi6desSljo9K1WY9+fF+3MSlbdgvVKlSrJ69eoC465gXMvWb37zGzuuL/8+8UT+p7PC19XSlqjUBRo3bmx+tmvXzpx1DIRLnX7gZNWq2F+82adPH5k//4fvT9Q/N97tx+PuhIQQQkg6kop+0jnZdwTrL6A+9dd97TaBRN8RrL8y5/8NnSS33f+02b64v3mkVJW6I0eO2HUtPbp81VVXSfPmze341Vdfbc7i6Zm2evXqJSx1Si/TM1JaqILtHnroIVN6evbsaX5edNFF5syZFio9a1erVi05/fTTzbZ6dkzfR9ejRw/zM7gNfT+cLnft2tVcL3jvnit83/TlXX0f3gUXXCCVK1e24yNGjDCX6d9FzxQ+++yzZtx9+bVTp07mV5wEt6n3Wc9a3nTTTbJ9+/aYUqcvX+t2ehv60rDSl1p1TP/dKlSoIJdccgmljhBCSKRJJmfzNym/I7hu6352+brbp8jqtTk/XPh/4v0KHPXoi2/IQ0+94g4fVd6XuqLQ98JpqfJFw4YNZevWre5w5NydkBBCCElHktHv8Q1zfwm1WrF6ndRp3df8wmn9ejiX+x3Bavu3u+T2B56Je3tHG6XOoWfu9AxUcNZKz1JlsiFDhkiLFi2kZs2a5sMLJZG7ExJCCCHpSDLD7nsqZj1eCVvy3sfmG0r27s+L+3Kq+x3BgU1fb5cz2g+UHd8VfEn3aKLUIXLuTkgIIYSkI8nMevmtmPV4pS489tjsBXLn5FmhS+NfJ/Dd7r3S6tpR7vBRRalD5NydkBBCCElHUkn1HcH6VXKL311jls+5YogsWP5h/hUl/ncEf/jpv+zyb4dMkvF//mvo0qOPUofIuTshIYQQko6kkuo7glWP308275ubMXeRHVPxPiDxxrLVcnH3O00BfGb+kpjLigOlDpFzd0JCCCEkHSltKHWInLsTEkIIIelIaUOpQ+TcnZAQQghJR0obSh0ix5dK+0Xnkzn1C3PqJ+bUP5Q6RI4Di18oAP5hTv3EnPqHUofIcWDxCwXAP8ypn5hT/1DqEDkOLH6hAPiHOfUTc+ofSh0ix4HFLxQA/zCnfmJO/UOpQ+Q4sPiFAuAf5tRPzKl/KHWIHAcWv1AA/MOc+ok59Q+lDpHjwOIXCoB/mFM/Maf+odQhchxY/EIB8A9z6ifm1D+UOkSOA4tfKAD+YU79xJz6h1KHyHFg8QsFwD/MqZ+YU/9Q6hA5Dix+oQD4hzn1E3PqH0odIseBxS8UAP8wp35iTv1DqUPkOLD4hQLgH+bUT8ypfyh1iFzN7MmEEJI0SD9KnX8odYice/AmhBA3SD9KnX8odYice/AmhBA3qXz1Ta6c+uu+MuqPz7kXGYPHPi41m99oEwiPaU5qeZO97L2P1kuDDoPkLy++Ycd8QqnzD6UOkXMP3oQQ4iaVoKjdPfV5GT7xaefS/FL3xZZt7nCM/+w7Vv7x9gdmedeefaYkqiv6jZN5C94Jb+oFSp1/KHWInHvwJoQQN8ncM+1Fmf7cP+x6+ExcIHymrn67m92LZV/eATm5VW+73v6m0bJ6bY5dj3ebmY5S5x9KHSLnHrwJIcRNMsPue0pmvfyWXU9VwF5fuloOH44tNB37j5fX/+8snbq4+52ycdNWu57qNjMRpc4/lDpEzj14E0KIm2S00N12/w8vuRamgL2/ZkPMunudm+99VOa+kf+S66FDh6V2qz4xl/uAUucfSh0i5x68CSHETSrx3lPXeeD9snTlWrOsH6QIXD/iYbusZr+2TK4dPiVmjPfUIRNR6opJ3bp1pUyZ2LvtrsdTmG0KIysry9yWplq1ajJ9+nR3k8i4B29CCHGTyuatuXJKVp+YT7+efflgc5ZN6Zm8hlfcIme0HygPPfWK3UZpIXRfjlXvfviZnHXZIPnzC6+7F3mBUucfSl0x0VJXsWJFWbZsmR0rTGFbuzb/f5lFpaVuxowZdl3vy9y5c3/YIELuwZsQQtz8FOd1GuoOIYRS5x9KXTHRUqcFLVzkwst///vfpWHDhibfffddgW2mTJkiAwYMsOPq7rvvNj8PHz4szZs3lwoVKsiYMWNitgm4pW7YsGEycOBAs7x//37p3LmzuX6zZs3sNm7p7NChg8ybN88s79u3T5o2bSpNmjQxf35A71PVqlWlXr168vzzz9vxZNyDNyGEuEH6Uer8Q6krJlrq1q9fb0qbFjgVLk1BWdKClaj4hZeXLl0aM759+3azfP3118uIESPsZYFwqfv888/NdYLyqNcNdu6tW7fK22+/bZa1KK5YscIsK/e+7N27V3bu3GnH9fY7duxotwmflUzGPXgTQogbpB+lzj+UumISlLojR47YEuSeCZs6dar06tVLKlWqZMfC21SpUsUWserVq5ufs2fPlmuvvdZuo9zbVVrqtKTp7bdv316OPfZYUyADy5cvN2cC9fJOnTqZsQMHDpiXaZW+VBsUtokTJ8qoUaPsdc8991xz5m7RokVy0kknxZxpdOl9c7NycBtCCCHkqKY0oNQVk6DUKX2J85lnnilw5ksLmtJiFB4PvPnmm9K6deuY8QkTJsj5559vylg4Lvfl148//ljKli1rlvv37y8tW7a0Z/t020Cwjf4M/lfXr18/ufjii2P+vG3b8n9T+8aNG00p1Pt355132ttJxt3xCCGEkHSnNKDUFZNwqVPBWarwup7F++CDDwqMh+l6ixYt7EukwdimTZvs+tNPF/yKHLfUXX755VK/fn2zfN5558lzz+V/YmzmzJkxpU6LnN6+vucuTMdyc/N/RcC6devMTz2bp38HpS/juvc9EXfHI4QQQtKdZM7tOFR6jZgqXQZPlOZX3+VebOivuBkzfY75VPXv75thxtZ89oVMfHy+TfCrdfRT17p85+RZctXA++TMy/Lfw360UeqKiVvq3n///ZjSo8WoVq1aMmnSJLNtwC1GDz74YIExLVL6kmq5cuXkwgsvlFWrVsVcrrSoBUVSX1IdNGhQzOX60qx+wOFvf/tbTKlTQeEM05dmW7VqJbVr15bRo0ebMS2kehayfPny5jYOHToUc51E3B2PEEIISXcSydn8jVzY7Xa7flrbAbJnX15oi4LcX1at/mfDJvmPzsPM8tSZr8b8Kpx42x8NlDokpQ+Q4H11R4u74xFCCCHpTiLudwePeHCWzJi7KLRFvjqt+8rYP71kft/hqv/Z6F5sfqeh/r5EpSdCtMiNfOhZc/bvrv//szhQ6pCQnm3Ts4eFPeP2U7k7HiGEEJLuJOJ+d/D4P/9VpjyT/1sqwrrf+qDs3Z9nvrVEvyvYFT4btz/voJzedoB8s32nLFz+kfnF2MWBUofIuTseIYQQku4k4n53sJa3f/73J6EtRF5fujpm3X05Vc/I6XvqAu1uvNe8HBto0GGQ5O7cbdePFkodIufueIQQQki6k0y4pMVb/nrbDln87hqzrC+xuqXOXb/9gWek2y0P2HX38qOFUofIuTseIYQQku4ko0Xt/CtvlSZdbpNvv9tjxvR9ceEPUPT4/WQ5uVVvuaLfuJjvCl6X85U0/k3+ByTCsh9+wbwPT2/zs39tcS8+Kih1iJy74xFCCCHpzo/12JyF5r1xmYRSh8i5Ox4hhBCS7pQGlDpEzt3xCCGEkHSnNKDUIXJ8qbRfdD6ZU78wp35iTv1DqUPkOLD4hQLgH+bUT8ypfyh1iBwHFr9QAPzDnPqJOfUPpQ6R48DiFwqAf5hTPzGn/qHUIXIcWPxCAfAPc+on5tQ/lDpEas2GLwkhhBDicVKh1HmiTBmm0jfMqX+YUyAzZOq+mpn3GgVk6gMQiTGn/mFOgcyQqftqZt5rFJCpD0Akxpz6hzkFMkOm7quZea9RQKY+AJEYc+of5hTIDJm6r2bmvQYAAEAMSh0AAIAHKHUAAAAeoNQBAAB4gFKX4TZs2GDe0HnHHXeYn9u3b3c3QQmUat7Wrl0rlStXlttvv13q168vZ511lr2sbt26kp2dbfLAAw+EroUo1ahRQ6666ipp3bq1NGjQwL1YevXqJUOGDLFzt23bNntZqusiGqn2UxXMp0a3ycvLM+Ph/VSDkqFRo0Zy/PHHS8+ePd2LLD3eXnrppWaf1H0zkAn7KaUuw+lBJPiKml27dkmlSpWcLVAS/dh5C38SS58sULLok3+9evXsuhbyPXv2hLbIL3U5OTkxYyredVEy/Jj99ODBg+ynGSRRqdu9e3fMPhjMY7z91N3HSwJKXYZzP3btrqNkcufJXXe5Txa6rlmyZEloK0Tltttuk0mTJtn1gQMHyvTp00Nb5Je6YN6qVKlix+NdFyWDu1+662Ht27eXZ5991q6H99PTTjsttCVKgkSlbtq0aTJo0CC7HrwaEm8/dffxkiDxIxQZwT3IuOsomdx5ctfD9H+E7777rjtsJLseik+fPn3kscces+t33XWXjB8/PrRFrFdeeUVOOOEEsxzvuigZ3P3LXQ9LdtmwYcOkc+fO7jAilKjUjRs3TkaOHGnXH330UfMz3n6abB+PSuJHITKCeyBx11EyufPkrgf0jM6KFSvcYSvR9VC89GDft29fu65nbRYuXBjaoqBg7uJdFyWDu3+564G5c+dKVlaWO2zt3LlTjjvuOHcYEUpU6hYsWBCzDwb7Zrz9NNU+HoX4j1BkDH1gTZ061Szfeuut0q9fP2cLlESJ5i38pFGxYkVZtWqVXQ8E7+PQ92clepJB8QvPRbzlTZs22TE9a9OuXTu7Hm97RC/RfqoFbvHixXa7smXL2g9IBMLvtzrjjDPMGSCUHG6pS7QPFma5JCmZ9wo/yvXXXy/lypUzn6xD5og3b8EbcfUJQw8a4QR69Ogh1apVk7Zt28qRI0fsOKL15ZdfSq1ataROnTqyY8cOM6bzE8yp/i9fX3KtWrWqjB07NnzVuNdFyRBvP61evbocOnTIrsd7gg/2U53zp59+2r0YEQk+pRw+ruo+17x5c7tNbm6u2Rd1nwz/ZywT9tOCj0QAAABkHEodAACAByh1AAAAHqDUAQAAeIBSBwAA4AFKHQAAgAcodQAAAB6g1AEAAHiAUgcAAOABSh0AAIAH/hc9mEIrpZI4dAAAAABJRU5ErkJggg==>